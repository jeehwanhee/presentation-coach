import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { StepHeader } from "../../components/StepHeader";
import { createPresentation } from "../../api/presentations";
import { usePresentationSession } from "../../session/usePresentationSession";
import { ApiError } from "../../api/client";

export function CreateScreen() {
  const navigate = useNavigate();
  const { setSession } = usePresentationSession();

  const [title, setTitle] = useState("");
  const [scriptOpen, setScriptOpen] = useState(false);
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !loading;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setLoading(true);
    setError(null);
    try {
      const res = await createPresentation({
        title: title.trim(),
        script: script.trim() ? script.trim() : null,
      });
      setSession({
        presentationId: res.presentation_id,
        resultToken: res.result_token,
        slideUploadUrl: res.slide_upload_url,
        audioUploadUrl: res.audio_upload_url,
      });
      navigate("/upload");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "발표를 생성하지 못했어요. 다시 시도해주세요.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-content">
      <StepHeader step={1} label="발표 정보" />

      <div className="app-heading">
        <h2>발표 리허설을 시작해볼까요?</h2>
        <p>제목만 입력하면 바로 다음 단계로 넘어갈 수 있어요.</p>
      </div>

      <form className="create-form" onSubmit={handleSubmit}>
        <label className="field">
          <span className="field-label">발표 제목</span>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="예: 졸업 발표 리허설"
            maxLength={255}
            autoFocus
          />
        </label>

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
                rows={8}
              />
            </div>
          )}
        </div>

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="cta-button" disabled={!canSubmit}>
          {loading ? "생성 중..." : "다음"}
        </button>
      </form>
    </div>
  );
}
