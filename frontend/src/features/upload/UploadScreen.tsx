import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { StepHeader } from "../../components/StepHeader";
import { UploadCard } from "./UploadCard";
import { useFileUpload, readAudioDurationMs } from "./useFileUpload";
import { submitPresentation } from "../../api/presentations";
import { usePresentationSession, type PresentationSession } from "../../session/usePresentationSession";
import { ApiError } from "../../api/client";

const AUDIO_DURATION_LIMIT_MS = 600_000; // 10분, docs/API_명세서.md §2.2

export function UploadScreen() {
  const navigate = useNavigate();
  const { getSession } = usePresentationSession();

  const [session, setLocalSession] = useState<PresentationSession | null>(null);
  const [audioDurationMs, setAudioDurationMs] = useState<number | null>(null);
  const [durationError, setDurationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const s = getSession();
    if (!s) {
      navigate("/", { replace: true });
      return;
    }
    setLocalSession(s);
  }, [getSession, navigate]);

  const slideUpload = useFileUpload(session?.slideUploadUrl ?? "");
  const audioUpload = useFileUpload(session?.audioUploadUrl ?? "");

  async function handleAudioSelect(file: File) {
    setDurationError(null);
    setAudioDurationMs(null);
    try {
      const durationMs = await readAudioDurationMs(file);
      if (durationMs > AUDIO_DURATION_LIMIT_MS) {
        setDurationError("오디오는 10분을 넘을 수 없어요.");
        return;
      }
      setAudioDurationMs(durationMs);
      await audioUpload.upload(file);
    } catch {
      setDurationError("오디오 길이를 확인하지 못했어요.");
    }
  }

  const requiredDone = slideUpload.status === "done" && audioUpload.status === "done" && audioDurationMs !== null;
  const completedCount = [slideUpload.status === "done", audioUpload.status === "done"].filter(Boolean).length;

  async function handleStartAnalysis() {
    if (!session || !requiredDone || audioDurationMs === null) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitPresentation(session.presentationId, session.resultToken, audioDurationMs);
      navigate(`/r/${session.presentationId}?token=${session.resultToken}`);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "제출하지 못했어요. 다시 시도해주세요.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!session) return null;

  return (
    <div className="app-content">
      <StepHeader step={2} label="자료 업로드" />

      <div className="app-heading">
        <h2>발표 자료를 업로드해주세요</h2>
        <p>PPT와 음성 파일은 필수예요.</p>
      </div>

      <div className="upload-grid">
        <UploadCard
          num={1}
          title="발표 슬라이드 (PPT)"
          accept=".ppt,.pptx"
          status={slideUpload.status}
          fileName={slideUpload.fileName}
          errorMessage={slideUpload.errorMessage}
          onSelect={(file) => slideUpload.upload(file)}
        />
        <UploadCard
          num={2}
          title="발표 음성 녹음"
          accept="audio/*"
          status={audioUpload.status}
          fileName={audioUpload.fileName}
          errorMessage={audioUpload.errorMessage ?? durationError}
          onSelect={handleAudioSelect}
        />
      </div>

      {submitError && <p className="error-text">{submitError}</p>}

      <div className="status-bar">
        <span className="progress-text">
          필수 항목 <b>{completedCount}/2</b> 완료
        </span>
        <button
          type="button"
          className="cta-button"
          disabled={!requiredDone || submitting}
          onClick={handleStartAnalysis}
        >
          {submitting ? "제출 중..." : "분석 시작하기"}
        </button>
      </div>
    </div>
  );
}
