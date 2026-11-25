import { useRef, useState } from "react";
import { portfolioApi } from "@/api/portfolio";
import { ApiError, toApiError } from "@/api/client";
import type { PortfolioImportResult } from "@/types";
import { Spinner } from "./StateViews";

interface PortfolioImportProps {
  onImported: () => void;
}

type Phase = "idle" | "uploading" | "done" | "error";

export function PortfolioImport({ onImported }: PortfolioImportProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<PortfolioImportResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function handleUpload() {
    if (!file) return;
    setPhase("uploading");
    setError(null);
    setResult(null);
    try {
      const res = await portfolioApi.importCsv(file);
      setResult(res);
      setPhase("done");
      onImported();
    } catch (err) {
      setError(toApiError(err));
      setPhase("error");
    }
  }

  function reset() {
    setFile(null);
    setResult(null);
    setError(null);
    setPhase("idle");
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="space-y-3">
      <p className="text-2xs text-slate-500">
        Import holdings from a Zerodha Console or Kite CSV export. Invalid rows are reported, never
        silently dropped.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="block text-xs text-slate-400 file:mr-3 file:rounded-md file:border-0 file:bg-surface-3 file:px-3 file:py-1.5 file:text-xs file:text-slate-200 hover:file:bg-line"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setPhase("idle");
            setResult(null);
            setError(null);
          }}
          disabled={phase === "uploading"}
        />
        <button
          type="button"
          className="btn-primary"
          onClick={handleUpload}
          disabled={!file || phase === "uploading"}
        >
          {phase === "uploading" ? <Spinner label="Uploading…" /> : "Import CSV"}
        </button>
        {(result || error) && phase !== "uploading" ? (
          <button type="button" className="btn-ghost" onClick={reset}>
            Clear
          </button>
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2">
          <p className="text-sm text-slate-200">{error.userMessage}</p>
        </div>
      ) : null}

      {result ? (
        <div className="rounded-md border border-line bg-surface-0 p-3">
          <div className="flex flex-wrap gap-2">
            <span className="pill bg-surface-3 text-slate-300">Parsed {result.total_parsed}</span>
            <span className="pill bg-pos/15 text-pos">Added {result.added}</span>
            <span className="pill bg-accent-soft text-slate-100">Updated {result.updated}</span>
            {result.zeroed.length > 0 ? (
              <span className="pill bg-warn/15 text-warn">Set to 0: {result.zeroed.length}</span>
            ) : null}
            <span
              className={`pill ${
                result.errors.length ? "bg-neg/20 text-neg" : "bg-surface-3 text-slate-400"
              }`}
            >
              Rejected {result.errors.length}
            </span>
          </div>
          {result.zeroed.length > 0 ? (
            <div className="mt-3">
              <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-slate-500">
                Not in the file, set to 0 (lent shares still show from contracts)
              </p>
              <p className="text-2xs text-slate-300">{result.zeroed.join(", ")}</p>
            </div>
          ) : null}
          {result.zeroing_skipped ? (
            <p className="mt-3 text-2xs text-warn">{result.zeroing_skipped}</p>
          ) : null}
          {result.errors.length > 0 ? (
            <div className="mt-3">
              <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-slate-500">
                Rejected rows
              </p>
              <ul className="max-h-40 space-y-1 overflow-y-auto text-2xs text-slate-400">
                {result.errors.map((e, i) => (
                  <li key={i} className="rounded bg-surface-2 px-2 py-1">
                    {e}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="mt-2 text-2xs text-pos">All rows imported successfully.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
