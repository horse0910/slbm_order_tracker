/**
 * Centralized HTTP client for the SLBM backend.
 *
 * All frontend network access goes through this module — components never call
 * `fetch()` directly. The browser only ever talks to the FastAPI backend; NSE
 * access stays server-side (Phase 1 NSE client + caching).
 */

export type ApiErrorKind =
  | "network" // backend unreachable / fetch failed
  | "forbidden" // 403 — NSE / market data access rejected
  | "rate_limited" // 429 — provider rate limiting
  | "not_found" // 404
  | "validation" // 400 / 422 — bad request payload
  | "server" // 5xx
  | "malformed" // response body could not be parsed
  | "unknown";

export interface FieldError {
  field: string;
  message: string;
}

/** Normalized error surfaced to the UI. Never carries a raw stack trace. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly fieldErrors: FieldError[];

  constructor(
    kind: ApiErrorKind,
    message: string,
    status: number | null = null,
    fieldErrors: FieldError[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.fieldErrors = fieldErrors;
  }

  /** A user-facing sentence appropriate for an error banner. */
  get userMessage(): string {
    switch (this.kind) {
      case "network":
        return "Unable to connect to the SLBM backend. Make sure the API server is running.";
      case "forbidden":
        return "Market-data access was temporarily rejected by the exchange (403). Please retry shortly.";
      case "rate_limited":
        return "The data provider is rate-limiting requests (429). Please wait a moment and retry.";
      case "not_found":
        return this.message || "The requested resource was not found.";
      case "validation":
        return this.message || "The request was rejected. Please check the input.";
      case "server":
        return "The backend encountered an error while processing the request.";
      case "malformed":
        return "The backend returned an unexpected response.";
      default:
        return this.message || "Something went wrong.";
    }
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function buildUrl(path: string): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${clean}`;
}

/**
 * Absolute URL for a backend `path`, honoring `VITE_API_BASE_URL` — the SAME
 * base the fetch client uses. Exposed for non-fetch flows (e.g. CSV download
 * anchors) so they can never drift from where the API actually lives.
 */
export function apiUrl(path: string): string {
  return buildUrl(path);
}

/** Extract a human-readable message + field errors from a FastAPI error body. */
function parseErrorBody(body: unknown): { message: string; fields: FieldError[] } {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    // FastAPI validation errors: detail is an array of {loc, msg, type}
    if (Array.isArray(detail)) {
      const fields: FieldError[] = detail.map((d) => {
        const loc = Array.isArray((d as { loc?: unknown[] }).loc)
          ? ((d as { loc: unknown[] }).loc.filter((p) => p !== "body") as unknown[])
          : [];
        return {
          field: loc.length ? loc.join(".") : "request",
          message: String((d as { msg?: unknown }).msg ?? "Invalid value"),
        };
      });
      return {
        message: fields.map((f) => `${f.field}: ${f.message}`).join("; ") || "Validation error",
        fields,
      };
    }
    if (typeof detail === "string") {
      return { message: detail, fields: [] };
    }
  }
  return { message: "Request failed", fields: [] };
}

function classify(status: number, message: string): ApiErrorKind {
  const lower = message.toLowerCase();
  if (status === 403 || lower.includes("403") || lower.includes("blocked")) return "forbidden";
  if (status === 429 || lower.includes("429") || lower.includes("rate limit")) return "rate_limited";
  if (status === 404) return "not_found";
  if (status === 400 || status === 422) return "validation";
  if (status >= 500) return "server";
  return "unknown";
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  isFormData?: boolean;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, isFormData = false, signal } = opts;

  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;

  if (body !== undefined) {
    if (isFormData) {
      payload = body as FormData; // browser sets multipart boundary
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path), { method, headers, body: payload, signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("network", "Network request failed", null);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      if (response.ok) {
        throw new ApiError("malformed", "Response was not valid JSON", response.status);
      }
    }
  }

  if (!response.ok) {
    const { message, fields } = parseErrorBody(parsed);
    throw new ApiError(classify(response.status, message), message, response.status, fields);
  }

  return parsed as T;
}

export const http = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: "GET", signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "POST", body, signal }),
  put: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PUT", body, signal }),
  patch: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PATCH", body, signal }),
  del: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: "DELETE", signal }),
  postForm: <T>(path: string, form: FormData, signal?: AbortSignal) =>
    request<T>(path, { method: "POST", body: form, isFormData: true, signal }),
};

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err instanceof DOMException && err.name === "AbortError") {
    return new ApiError("unknown", "Request aborted");
  }
  return new ApiError("unknown", "Unexpected error");
}
