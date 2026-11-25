import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, http } from "@/api/client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiError classification", () => {
  it("classifies a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("failed to fetch")));
    await expect(http.get("/api/health")).rejects.toMatchObject({ kind: "network" });
  });

  it("classifies a 403 as forbidden with a friendly message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(403, { detail: "blocked" })));
    try {
      await http.get("/api/slb/live/RELIANCE");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).kind).toBe("forbidden");
      expect((err as ApiError).userMessage).toMatch(/temporarily rejected/i);
    }
  });

  it("classifies a 429 as rate limited", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(429, { detail: "slow down" })));
    await expect(http.get("/api/slb/eligible")).rejects.toMatchObject({ kind: "rate_limited" });
  });

  it("extracts FastAPI validation field errors", async () => {
    const body = {
      detail: [{ loc: ["body", "symbol"], msg: "field required", type: "value_error.missing" }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, body)));
    try {
      await http.post("/api/portfolio", {});
      throw new Error("should have thrown");
    } catch (err) {
      const e = err as ApiError;
      expect(e.kind).toBe("validation");
      expect(e.fieldErrors[0]).toMatchObject({ field: "symbol", message: "field required" });
    }
  });

  it("returns parsed JSON on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { status: "healthy" })));
    await expect(http.get("/api/health")).resolves.toEqual({ status: "healthy" });
  });
});
