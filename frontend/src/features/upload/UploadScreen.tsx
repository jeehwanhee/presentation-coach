import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { StepHeader } from "../../components/StepHeader";
import { UploadCard } from "./UploadCard";
import { AudioUploadCard } from "./AudioUploadCard";
import { readAudioDurationMs } from "./useFileUpload";
import { createPresentation, submitPresentation, uploadToPresignedUrl } from "../../api/presentations";
import { usePresentationSession } from "../../session/usePresentationSession";
import { ApiError } from "../../api/client";

const AUDIO_DURATION_LIMIT_MS = 600_000; // 10분, docs/API_명세서.md §2.2

// 심사용 예시 리포트 — presentation_id/token 준비되면 여기 채우기(비어있으면 섹션 자체가 안 보임).
const EXAMPLE_REPORTS: { label: string; tone: "good" | "bad"; id: number; token: string }[] = [];

// 심사용 테스트 기능 — PPT/대본은 공통, 음성만 잘한 예시/못한 예시로 다르게 채움.
const EXAMPLE_SCRIPT = `안녕하세요, 발표 리허설 코치 PTPT를 소개해드리겠습니다. 많은 분들이 발표를 준비하면서 이런 고민을 하십니다. 내가 지금 잘하고 있는 건지, 어디를 고쳐야 하는지 스스로는 알기가 어렵다는 거죠. PTPT는 이 문제를 해결하기 위해 만들었습니다.
사용 방법은 간단합니다. 발표 슬라이드와 리허설 음성만 올리면, AI가 자동으로 분석해줍니다. 먼저 슬라이드에 적힌 주장이 실제 발화에서 제대로 뒷받침되는지 확인하고, 주제에서 벗어난 이야기나 논리적으로 비약된 부분이 있는지도 짚어줍니다. 그리고 말하기 속도, 침묵 구간, 채움말 사용 빈도 같은 전달력 지표도 함께 제공합니다. 대본이 있다면 실제 발화와 얼마나 차이가 나는지도 비교해드립니다.
로그인 없이 제목만 입력하면 바로 시작할 수 있고, 분석이 끝나면 링크 하나로 3일간 결과를 확인하실 수 있습니다. 발표 전에 꼭 한 번, PTPT로 리허설해보세요. 감사합니다.`;

async function fetchAsFile(url: string, filename: string, mimeType: string): Promise<File> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`예시 파일을 불러오지 못했어요 (${res.status})`);
  const blob = await res.blob();
  return new File([blob], filename, { type: mimeType });
}

function SlidesIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 19v3" />
    </svg>
  );
}

export function UploadScreen() {
  const navigate = useNavigate();
  const { getDraft, clearDraft } = usePresentationSession();

  const [title, setTitle] = useState<string | null>(null);
  const [script, setScript] = useState("");

  const [slideFile, setSlideFile] = useState<File | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioDurationMs, setAudioDurationMs] = useState<number | null>(null);
  const [durationError, setDurationError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [loadingExample, setLoadingExample] = useState<"good" | "bad" | null>(null);

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

  async function fillExample(tone: "good" | "bad") {
    setLoadingExample(tone);
    setSubmitError(null);
    try {
      setScript(EXAMPLE_SCRIPT);
      const ppt = await fetchAsFile(
        "/examples/slides.pptx",
        "PTPT_예시_슬라이드.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      );
      setSlideFile(ppt);

      // 음성 예시는 용량·개인 녹음이라 git엔 안 올리고 서버 파일시스템(EC2)에서만 서빙(WebConfig.java 참고).
      const audioUrl = `${import.meta.env.VITE_API_BASE_URL}/api/examples/${tone === "good" ? "good" : "bad"}.m4a`;
      const audioName = tone === "good" ? "잘한예시.m4a" : "못한예시.m4a";
      const audio = await fetchAsFile(audioUrl, audioName, "audio/mp4");
      await handleAudioSelect(audio);
    } catch {
      setSubmitError("예시 자료를 불러오지 못했어요. 다시 시도해주세요.");
    } finally {
      setLoadingExample(null);
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
      <StepHeader />

      <div className="app-heading">
        <h2>발표 자료를 업로드해주세요</h2>
        <p>
          PPT와 음성 파일은 <span className="required-text">필수</span>예요.
        </p>
      </div>

      <div className="example-fill">
        <p className="example-fill-title">지금 분석할 자료가 없다면?</p>
        <p className="field-hint">테스트용 예시 자료로 빠르게 채워보기 (PPT·대본은 동일, 음성만 다름)</p>
        <div className="example-fill-buttons">
          <button
            type="button"
            className="example-fill-btn tone-good"
            onClick={() => fillExample("good")}
            disabled={submitting || loadingExample !== null}
          >
            {loadingExample === "good" ? "불러오는 중..." : "잘한 예시로 채우기"}
          </button>
          <button
            type="button"
            className="example-fill-btn tone-bad"
            onClick={() => fillExample("bad")}
            disabled={submitting || loadingExample !== null}
          >
            {loadingExample === "bad" ? "불러오는 중..." : "못한 예시로 채우기"}
          </button>
        </div>
      </div>

      <div className="upload-panel">
        <div className="upload-grid">
          <UploadCard
            icon={<SlidesIcon />}
            title="발표 슬라이드 (PPT)"
            hint={
              <>
                지원 형식: <span className="highlight-red">PPT, PPTX</span>
              </>
            }
            required
            accept=".ppt,.pptx"
            status={submitting ? "uploading" : slideFile ? "selected" : "empty"}
            fileName={slideFile?.name ?? null}
            fileSize={slideFile?.size ?? null}
            errorMessage={null}
            onSelect={(file) => setSlideFile(file)}
            onClear={() => setSlideFile(null)}
          />
          <AudioUploadCard
            icon={<MicIcon />}
            title="발표 음성 녹음"
            hint={
              <>
                파일 업로드(<span className="highlight-red">MP3, WAV, M4A, WEBM 등</span>) 또는 마이크로 직접 녹음
              </>
            }
            required
            status={submitting ? "uploading" : durationError ? "error" : audioFile ? "selected" : "empty"}
            fileName={audioFile?.name ?? null}
            fileSize={audioFile?.size ?? null}
            errorMessage={durationError}
            onSelect={handleAudioSelect}
            onClear={() => {
              setAudioFile(null);
              setAudioDurationMs(null);
              setDurationError(null);
            }}
          />
        </div>
      </div>

      <div className="script-field">
        <div className="script-field-head">
          <span className="tag optional">선택</span>
          <span className="card-title">발표 대본 추가</span>
        </div>
        <p className="field-hint">
          없어도 분석할 수 있어요. 있으면 대본과 실제 발화를 비교한 피드백을 더 받을 수 있어요.
        </p>
        <textarea
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder="발표 대본을 붙여넣어주세요"
          rows={5}
        />
      </div>

      {submitError && <p className="error-text">{submitError}</p>}

      <div className="upload-progress">
        <div className="upload-progress-bar" style={{ width: `${(completedCount / 2) * 100}%` }} />
      </div>

      <div className="status-bar">
        <span className="progress-text">
          <span className="required-text">필수</span> 항목 <b>{completedCount}/2</b> 완료
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

      {EXAMPLE_REPORTS.length > 0 && (
        <div className="example-reports">
          <p className="field-hint">심사용 예시 리포트</p>
          <div className="example-reports-links">
            {EXAMPLE_REPORTS.map((ex) => (
              <Link
                key={ex.label}
                to={`/r/${ex.id}?token=${ex.token}`}
                target="_blank"
                rel="noopener noreferrer"
                className={`example-report-link tone-${ex.tone}`}
              >
                {ex.label} 리포트 보기
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
