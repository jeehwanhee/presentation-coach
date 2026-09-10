import { useCallback } from "react";

// STEP 1(제목)에서 STEP 2(대본+파일)로 넘어갈 때 필요한 값은 title 하나뿐 —
// create()는 이제 STEP 2에서 파일 선택 후 "분석 시작하기" 시점에 한 번에 호출한다
// (제목+대본+파일을 다 모은 뒤에 create→업로드→submit을 연달아 처리).
// 그래서 sessionStorage엔 이 짧은 draft만 두면 됨. presentation_id/result_token은
// STEP 3(리포트 화면) URL(/r/:id?token=)로 바로 넘어가니 별도 저장이 필요 없음.

interface Draft {
  title: string;
}

const STORAGE_KEY = "presentation-coach:draft";

export function usePresentationSession() {
  const getDraft = useCallback((): Draft | null => {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as Draft;
    } catch {
      return null;
    }
  }, []);

  const setDraft = useCallback((draft: Draft) => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  }, []);

  const clearDraft = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
  }, []);

  return { getDraft, setDraft, clearDraft };
}
