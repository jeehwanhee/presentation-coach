import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { StepHeader } from "../../components/StepHeader";
import { UploadCard } from "./UploadCard";
import { readAudioDurationMs } from "./useFileUpload";
import { createPresentation, submitPresentation, uploadToPresignedUrl } from "../../api/presentations";
import { usePresentationSession } from "../../session/usePresentationSession";
import { ApiError } from "../../api/client";

const AUDIO_DURATION_LIMIT_MS = 600_000; // 10분, docs/API_명세서.md §2.2

export function UploadScreen() {
  const navigate = useNavigate();
  const { getDraft, clearDraft } = usePresentationSession();

  const [title, setTitle] = useState<string | null>(null);
  const [scriptOpen, setScriptOpen] = useState(false);
  const [script, setScript] = useState("");

  const [slideFile, setSlideFile] = useState<File | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioDurationMs, setAudioDurationMs] = useState<number | null>(null);
  const [durationError, setDurationError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const draft = getDraft();
    if (!draft) {
      navigate("/", { replace: true });
      return;
    }
    setTitle(draft.title);
  }, [getDraft, navigate]);

  async function handleAudioSelect(file: File) {
    setDurationError(null);
    setAudioDurationMs(null);
    setAudioFile(null);
    try {
      const durationMs = await readAudioDurationMs(file);
      if (durationMs > AUDIO_DURATION_LIMIT_MS) {
        setDurationError("오디오는 10분을 넘을 수 없어요.");
        return;
      }
      setAudioDurationMs(durationMs);
      setAudioFile(file);
    } catch {
      setDurationError("오디오 길이를 확인하지 못했어요.");
    }
  }

  const requiredDone = Boolean(slideFile) && Boolean(audioFile) && audioDurationMs !== null;
  const completedCount = [Boolean(slideFile), Boolean(audioFile)].filter(Boolean).length;

  async function handleStartAnalysis() {
    if (!title || !slideFile || !audioFile || audioDurationMs === null || !requiredDone) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createPresentation({
        title,
        script: script.trim() ? script.trim() : null,
      });
      await uploadToPresignedUrl(created.slide_upload_url, slideFile);
      await uploadToPresignedUrl(created.audio_upload_url, audioFile);
      await submitPresentation(created.presentation_id, created.result_token, audioDurationMs);

      clearDraft();
      navigate(`/r/${created.presentation_id}?token=${created.result_token}`);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "제출하지 못했어요. 다시 시도해주세요.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!title) return null;

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
          status={submitting ? "uploading" : slideFile ? "selected" : "empty"}
          fileName={slideFile?.name ?? null}
          errorMessage={null}
          onSelect={(file) => setSlideFile(file)}
        />
        <UploadCard
          num={2}
          title="발표 음성 녹음"
          accept="audio/*"
          status={submitting ? "uploading" : durationError ? "error" : audioFile ? "selected" : "empty"}
          fileName={audioFile?.name ?? null}
          errorMessage={durationError}
          onSelect={handleAudioSelect}
        />
      </div>

      <div className="script-field">
        <button
          type="button"
          className="script-toggle"
          onClick={() => setScriptOpen((v) => !v)}
          aria-expanded={scriptOpen}
        >
          <span className="tag optional">선택</span>
          발표 대본 추가 {scriptOpen ? "▲" : "▼"}
        </button>
        {scriptOpen && (
          <div className="script-body">
            <p className="field-hint">
              없어도 분석할 수 있어요. 있으면 대본과 실제 발화를 비교한 피드백을 더 받을 수 있어요.
            </p>
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder="발표 대본을 붙여넣어주세요"
              rows={6}
            />
          </div>
        )}
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
