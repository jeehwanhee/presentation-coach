import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { StepHeader } from "../../components/StepHeader";
import { usePollReport } from "./usePollReport";
import { ConsistencySection } from "./ConsistencySection";
import { DeliverySection } from "./DeliverySection";
import { ScriptDiffSection } from "./ScriptDiffSection";
import { ApiError } from "../../api/client";

const ERROR_MESSAGE: Record<string, string> = {
  STT_FAILED: "음성 인식에 실패했어요.",
  PPTX_PARSE_FAILED: "발표 자료를 분석하지 못했어요.",
  AUDIO_NOT_FOUND: "오디오 파일을 찾지 못했어요.",
  LLM_FAILED: "분석 중 오류가 발생했어요.",
  TIMEOUT: "분석 시간이 초과됐어요.",
};

export function ReportScreen() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const presentationId = Number(id);

  const { data, fatalError } = usePollReport(presentationId, token);

  if (fatalError instanceof ApiError && fatalError.status === 410) {
    return <StatusScreen title="만료된 리포트예요" message="이 발표는 생성 3일 후 자동으로 만료돼요." />;
  }
  if (fatalError instanceof ApiError && (fatalError.status === 403 || fatalError.status === 404)) {
    return <StatusScreen title="접근할 수 없어요" message="링크가 올바른지 확인해주세요." />;
  }
  if (fatalError) {
    return <StatusScreen title="문제가 발생했어요" message={fatalError.message} />;
  }

  if (!data || data.status === "PENDING" || data.status === "PROCESSING") {
    return (
      <div className="app-content">
        <StepHeader />
        <div className="processing-state">
          <div className="spinner" />
          <p>발표를 분석하고 있어요. 잠시만 기다려주세요...</p>
        </div>
      </div>
    );
  }

  if (data.status === "FAILED") {
    const code = data.error?.code ?? "";
    return (
      <StatusScreen
        title="분석에 실패했어요"
        message={ERROR_MESSAGE[code] ?? data.error?.message ?? "알 수 없는 오류가 발생했어요."}
      />
    );
  }

  const report = data.report!;

  return (
    <div className="app-content">
      <StepHeader />

      <ShareNotice />

      <ConsistencySection consistency={report.consistency} />

      {report.off_topic.length > 0 && (
        <section className="report-section">
          <h3>주제 이탈</h3>
          <ul className="simple-list">
            {report.off_topic.map((seg, i) => (
              <li key={i}>
                <p>{seg.text}</p>
                <span className="field-hint">{seg.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.logic_gaps.length > 0 && (
        <section className="report-section">
          <h3>논리 비약</h3>
          <ul className="simple-list">
            {report.logic_gaps.map((gap, i) => (
              <li key={i}>
                <p>{gap.text}</p>
                <span className="field-hint">{gap.note}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <DeliverySection delivery={report.delivery} />

      {report.script_diff && <ScriptDiffSection scriptDiff={report.script_diff} />}
    </div>
  );
}

function ShareNotice() {
  const [copied, setCopied] = useState(false);
  const url = window.location.href;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // 클립보드 권한이 없는 환경 등 — 실패해도 URL은 이미 화면에 보여서 수동 복사 가능.
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="share-notice">
      <button type="button" className="share-save-btn" onClick={handleCopy}>
        {copied ? "복사됨 ✓" : "리포트 저장하기"}
      </button>
      <code className="share-url">{url}</code>
    </div>
  );
}

function StatusScreen({ title, message }: { title: string; message: string }) {
  return (
    <div className="app-content">
      <div className="processing-state">
        <h2>{title}</h2>
        <p>{message}</p>
      </div>
    </div>
  );
}
