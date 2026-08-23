import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import type { Keyword } from "@/types";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
const ACTIVE_POLL_MS = 3_000;
const IDLE_POLL_MS = 30_000;

function responseFingerprint(rows: Keyword[]): string {
  return JSON.stringify(rows);
}

export function useKeywordSummaries() {
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const mountedRef = useRef(true);
  const fingerprintRef = useRef("");
  const inFlightRef = useRef<Promise<Keyword[]> | null>(null);

  const request = useCallback((): Promise<Keyword[]> => {
    if (!mountedRef.current) return Promise.resolve([]);
    if (inFlightRef.current) return inFlightRef.current;

    const nextRequest = apiGet<any>("/keywords?detail=summary")
      .then((response) => {
        const rows = Array.isArray(response.data) ? response.data as Keyword[] : [];
        if (!mountedRef.current) return rows;

        const fingerprint = responseFingerprint(rows);
        if (fingerprint !== fingerprintRef.current) {
          fingerprintRef.current = fingerprint;
          setKeywords(rows);
        }
        setError("");
        return rows;
      })
      .catch((reason: unknown) => {
        if (mountedRef.current && !fingerprintRef.current) {
          setError(reason instanceof Error ? reason.message : "关键词状态加载失败");
        }
        throw reason;
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
        inFlightRef.current = null;
      });

    inFlightRef.current = nextRequest;
    return nextRequest;
  }, []);

  const refresh = useCallback((force = false): Promise<Keyword[]> => {
    const current = inFlightRef.current;
    if (!force || !current) return request();
    return current.catch(() => []).then(() => request());
  }, [request]);

  useEffect(() => {
    mountedRef.current = true;
    refresh().catch(() => {});
    return () => {
      mountedRef.current = false;
    };
  }, [refresh]);

  const hasActiveRuns = useMemo(
    () => keywords.some((keyword) => ACTIVE_STATUSES.has(keyword.latest_run?.status || "")),
    [keywords],
  );

  useEffect(() => {
    const delay = hasActiveRuns ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    let timer = 0;
    let cancelled = false;

    const schedule = () => {
      if (cancelled) return;
      timer = window.setTimeout(async () => {
        if (document.visibilityState === "visible") {
          await refresh().catch(() => {});
        }
        if (cancelled) return;
        schedule();
      }, delay);
    };

    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [hasActiveRuns, refresh]);

  useEffect(() => {
    const onFocus = () => refresh().catch(() => {});
    const onVisibility = () => {
      if (document.visibilityState === "visible") onFocus();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  return { keywords, loading, error, refresh, hasActiveRuns };
}
