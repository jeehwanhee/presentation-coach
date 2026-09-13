import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePresentationSession } from "../../session/usePresentationSession";
import { ReportPreview } from "./ReportPreview";

export function CreateScreen() {
  const navigate = useNavigate();
  const { setDraft } = usePresentationSession();

  const [title, setTitle] = useState("");

  const canSubmit = title.trim().length > 0;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setDraft({ title: title.trim() });
    navigate("/upload");
  }

  return (
    <div className="app-content">
      <div className="hero">
        <h1 className="hero-brand">
          PTPT
          <br />
          <span className="hero-brand-sub">- Presentation Personal Training</span>
        </h1>
        <p className="hero-lead">발표 리허설을 시작해볼까요?</p>
        <p className="hero-desc">
          슬라이드와 발표 음성을 올리면, 슬라이드 주장이 실제 발화에서 뒷받침됐는지부터 말하기
          속도·침묵·채움말까지 리포트로 확인할 수 있어요.
        </p>
      </div>

      <ul className="feature-grid">
        <li className="feature-item">
          <span className="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          </span>
          <div>
            <p className="feature-title">발표자료 정합 검사</p>
            <p className="feature-desc">슬라이드 주장이 실제 발화에서 뒷받침됐는지 원문 인용과 함께 판정</p>
          </div>
        </li>
        <li className="feature-item">
          <span className="feature-icon c2">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="9" />
              <path d="m14.5 9.5-2 5-5 2 2-5z" />
            </svg>
          </span>
          <div>
            <p className="feature-title">주제 이탈 · 논리 비약 감지</p>
            <p className="feature-desc">슬라이드 범위를 벗어난 구간, 근거 없이 넘어간 구간을 짚어드려요</p>
          </div>
        </li>
        <li className="feature-item">
          <span className="feature-icon c3">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 20V10M12 20V4M20 20v-7" />
            </svg>
          </span>
          <div>
            <p className="feature-title">전달 지표</p>
            <p className="feature-desc">말하기 속도(WPM), 침묵 구간, 채움말 빈도, 성량 변화까지</p>
          </div>
        </li>
        <li className="feature-item">
          <span className="feature-icon c4">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 4h9l3 3v13H6z" />
              <path d="M9 12h6M9 16h6M9 8h3" />
            </svg>
          </span>
          <div>
            <p className="feature-title">대본 대조 (선택)</p>
            <p className="feature-desc">대본을 넣으면 실제 발화와 얼마나 다른지도 비교해드려요</p>
          </div>
        </li>
      </ul>

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

        <button type="submit" className="cta-button" disabled={!canSubmit}>
          다음
        </button>
      </form>

      <ReportPreview />

      <p className="usage-limit-notice">서비스 안정을 위해 하루 최대 10회까지 이용할 수 있어요.</p>
    </div>
  );
}
