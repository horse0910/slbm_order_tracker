import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, toApiError } from "@/api/client";

export interface UseApiResourceOptions {
  /** Poll interval in ms. 0 / undefined disables polling. */
  pollIntervalMs?: number;
  /** Whether the resource should fetch at all (e.g. gated on a selected symbol). */
  enabled?: boolean;
}

export interface ApiResourceState<T> {
  data: T | null;
  error: ApiError | null;
  /** True only during the very first load (no data yet). Drives skeletons. */
  loading: boolean;
  /** True during a background poll/refresh while data is already present. */
  refreshing: boolean;
  /** Timestamp (ms) of the last successful fetch, for a "last updated" label. */
  lastUpdated: number | null;
  refetch: () => void;
}

/**
 * Generic data hook with controlled polling.
 *
 * Concurrency model (see H-2):
 *  - Every fetch carries a monotonic generation token; only the latest
 *    generation may commit to state, so a slow/aborted response can never
 *    overwrite newer data.
 *  - A dependency change or manual refetch is a "force" run: it aborts any
 *    in-flight request and starts a fresh one (never skipped). This is the fix
 *    for the previous bug where a new request could be dropped because the
 *    aborted request had not yet cleared the in-flight flag.
 *  - A poll tick is a non-forced run: it is skipped if a request is already in
 *    flight, so polling never overlaps or duplicates.
 *  - Timers are cleared and the active request aborted on unmount / dep change;
 *    no state is written after unmount.
 */
export function useApiResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown>,
  options: UseApiResourceOptions = {},
): ApiResourceState<T> {
  const { pollIntervalMs = 0, enabled = true } = options;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const genRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  // Keep the latest fetcher without making it a dependency of the effect.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const hasDataRef = useRef(false);

  const run = useCallback(
    (opts?: { force?: boolean }) => {
      if (!enabled) return;
      const force = opts?.force ?? false;

      if (inFlightRef.current) {
        if (!force) return; // poll tick: don't overlap an in-flight request
        abortRef.current?.abort(); // forced run: cancel the stale request
      }

      const myGen = ++genRef.current;
      const controller = new AbortController();
      abortRef.current = controller;
      inFlightRef.current = true;

      if (hasDataRef.current) setRefreshing(true);
      else setLoading(true);

      const isCurrent = () => myGen === genRef.current;

      fetcherRef.current(controller.signal)
        .then((result) => {
          if (!isCurrent() || !mountedRef.current || controller.signal.aborted) return;
          setData(result);
          hasDataRef.current = true;
          setError(null);
          setLastUpdated(Date.now());
        })
        .catch((err) => {
          if (!isCurrent() || controller.signal.aborted || !mountedRef.current) return;
          setError(toApiError(err));
        })
        .finally(() => {
          // Only the current generation owns the in-flight flag / spinners; a
          // superseded (aborted) request must not clear a newer request's state.
          if (isCurrent()) {
            inFlightRef.current = false;
            if (mountedRef.current) {
              setLoading(false);
              setRefreshing(false);
            }
          }
        });
    },
    [enabled],
  );

  // Fetch on mount / when deps change, then poll on an interval.
  useEffect(() => {
    mountedRef.current = true;

    if (!enabled) {
      setLoading(false);
      return () => {
        mountedRef.current = false;
      };
    }

    // Reset "has data" so dep changes show a skeleton, not stale content.
    hasDataRef.current = false;
    setLoading(true);
    run({ force: true });

    let timer: ReturnType<typeof setInterval> | null = null;
    if (pollIntervalMs && pollIntervalMs > 0) {
      timer = setInterval(() => {
        run();
      }, pollIntervalMs);
    }

    return () => {
      mountedRef.current = false;
      if (timer) clearInterval(timer);
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, pollIntervalMs, enabled, ...deps]);

  const refetch = useCallback(() => {
    run({ force: true });
  }, [run]);

  return { data, error, loading, refreshing, lastUpdated, refetch };
}
