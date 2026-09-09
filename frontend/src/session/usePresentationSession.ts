import { useCallback } from "react";

// STEP 1 -> STEP 2 사이에서만 필요한 값들. sessionStorage라 새로고침엔 살아남고
// 탭을 닫으면 사라짐 (result_token이 민감정보 성격이라 localStorage보다 적절).
// STEP 3(리포트 화면)는 공유 링크로 새 탭에서 들어올 수 있어서 이 세션에 의존하지 않고
// URL 쿼리(?token=)를 우선으로 씀 — 구현설계.md §2.3 참고.

export interface PresentationSession {
  presentationId: number;
  resultToken: string;
  slideUploadUrl: string;
  audioUploadUrl: string;
}

const STORAGE_KEY = "presentation-coach:session";

export function usePresentationSession() {
  const getSession = useCallback((): PresentationSession | null => {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as PresentationSession;
    } catch {
      return null;
    }
  }, []);

  const setSession = useCallback((session: PresentationSession) => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  }, []);

  const clearSession = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
  }, []);

  return { getSession, setSession, clearSession };
}
