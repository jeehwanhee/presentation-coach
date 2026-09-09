import { useEffect, useRef, useState } from "react";
import { getPresentation } from "../../api/presentations";
import { ApiError } from "../../api/client";
import type { GetPresentationResponse } from "../../types/api";

const POLL_INTERVAL_MS = 2500; // docs/API_명세서.md §2.3 "폴링 주기 2~3초"

export function usePollReport(presentationId: number, token: string) {
  const [data, setData] = useState<GetPresentationResponse | null>(null);
  const [fatalError, setFatalError] = useState<ApiError | Error | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await getPresentation(presentationId, token);
        if (cancelled) return;
        setData(res);
        if (res.status === "DONE" || res.status === "FAILED") {
          return; // 종료 상태 — 더 이상 폴링 안 함.
        }
        timerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        // 403/410 같은 건 재시도해도 똑같이 실패하니 폴링 중단하고 바로 에러 화면으로.
        setFatalError(err instanceof Error ? err : new Error("알 수 없는 오류가 발생했어요."));
      }
    }

    poll();

    return () => {
      cancelled = true;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, [presentationId, token]);

  return { data, fatalError };
}
