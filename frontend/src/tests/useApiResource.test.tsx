import { describe, expect, it } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useApiResource } from "@/hooks/useApiResource";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface Call {
  symbol: string;
  signal: AbortSignal;
  d: Deferred<string>;
}

describe("useApiResource concurrency", () => {
  it("forces a new request on dependency change and ignores the stale one", async () => {
    const calls: Call[] = [];
    const fetcherFor = (symbol: string) => (signal: AbortSignal) => {
      const d = deferred<string>();
      calls.push({ symbol, signal, d });
      return d.promise;
    };

    const { result, rerender } = renderHook(
      ({ sym }) => useApiResource<string>((signal) => fetcherFor(sym)(signal), [sym]),
      { initialProps: { sym: "A" } },
    );

    // 1. Request A started on mount.
    await waitFor(() => expect(calls.some((c) => c.symbol === "A")).toBe(true));
    const callA = calls.find((c) => c.symbol === "A")!;

    // 2 + 3. Dependency changes to B; A's request must be aborted.
    act(() => {
      rerender({ sym: "B" });
    });
    expect(callA.signal.aborted).toBe(true);

    // 4 + 5. Request B must actually execute (not skipped by the in-flight guard).
    await waitFor(() => expect(calls.some((c) => c.symbol === "B")).toBe(true));
    const callB = calls.find((c) => c.symbol === "B")!;

    // 6. B resolves and its data is committed.
    await act(async () => {
      callB.d.resolve("data-B");
    });
    expect(result.current.data).toBe("data-B");

    // 7. A resolves late — it must NOT overwrite B's newer data.
    await act(async () => {
      callA.d.resolve("data-A");
    });
    expect(result.current.data).toBe("data-B");
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("does not fetch while disabled, then fetches once enabled", async () => {
    const calls: Call[] = [];
    const fetcher = (signal: AbortSignal) => {
      const d = deferred<string>();
      calls.push({ symbol: "x", signal, d });
      return d.promise;
    };

    const { result, rerender } = renderHook(
      ({ on }) => useApiResource<string>(fetcher, [on], { enabled: on }),
      { initialProps: { on: false } },
    );

    expect(calls.length).toBe(0);
    expect(result.current.loading).toBe(false);

    act(() => rerender({ on: true }));
    await waitFor(() => expect(calls.length).toBe(1));
    await act(async () => calls[0].d.resolve("ok"));
    expect(result.current.data).toBe("ok");
  });
});
