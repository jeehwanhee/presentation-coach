import { useEffect, useRef, useState } from "react";

interface CountUpOptions {
  duration?: number;
  hold?: number;
  delay?: number;
}

/**
 * 0 -> target까지 세다가 잠깐 멈추고, 다시 0으로 돌아가 반복하는 카운트업.
 * 실제 영상 없이도 "리포트가 살아있다"는 느낌을 주기 위한 무한 루프 애니메이션.
 */
function useLoopingCount(target: number, { duration = 1100, hold = 2400, delay = 0 }: CountUpOptions = {}) {
  const [value, setValue] = useState(0);
  const targetRef = useRef(target);
  targetRef.current = target;

  useEffect(() => {
    let raf = 0;
    let phase: "delay" | "count" | "hold" = "delay";
    let phaseStart = 0;

    function tick(ts: number) {
      if (!phaseStart) phaseStart = ts;
      const elapsed = ts - phaseStart;

      if (phase === "delay") {
        if (elapsed >= delay) {
          phase = "count";
          phaseStart = ts;
        }
      } else if (phase === "count") {
        const progress = Math.min(elapsed / duration, 1);
        setValue(Math.round(progress * targetRef.current));
        if (progress >= 1) {
          phase = "hold";
          phaseStart = ts;
        }
      } else {
        if (elapsed >= hold) {
          phase = "count";
          phaseStart = ts;
          setValue(0);
        }
      }
      raf = requestAnimationFrame(tick);
    }

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [duration, hold, delay]);

  return value;
}

const EXAMPLES = [
  {
    slide: "Slide 3",
    verdict: "NOT_MENTIONED",
    label: "미언급",
    claim: `"도입 3개월 만에 매출이 2배 늘었습니다"`,
    evidence: "발화에서 이 내용이 언급되지 않았어요",
  },
  {
    slide: "Slide 5",
    verdict: "NO_BASIS",
    label: "근거없음",
    claim: `"이용자 만족도 4.8점을 기록했습니다"`,
    evidence: `"...만족도가 꽤 높았다고 들었어요" (구체적 수치 언급 없음)`,
  },
  {
    slide: "Slide 2",
    verdict: "SUPPORTED",
    label: "뒷받침됨",
    claim: `"체류 시간이 40% 증가했습니다"`,
    evidence: `"실제로 체류 시간이 40% 정도 늘어난 걸 확인했어요"`,
  },
] as const;

export function ReportPreview() {
  const wpm = useLoopingCount(132, { delay: 0 });
  const fillers = useLoopingCount(12, { delay: 150 });
  const silences = useLoopingCount(3, { delay: 300 });

  const [exampleIndex, setExampleIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setExampleIndex((i) => (i + 1) % EXAMPLES.length);
    }, 3600);
    return () => clearInterval(id);
  }, []);

  const example = EXAMPLES[exampleIndex];

  return (
    <div className="report-preview">
      <span className="report-preview-badge">예시 리포트</span>

      <div className="delivery-stats">
        <div className="stat">
          <span className="stat-value">
            {wpm}
            <span className="stat-rating tone-good">적정</span>
          </span>
          <span className="stat-label">말하기 속도(WPM)</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {fillers}
            <span className="stat-rating tone-high">많음</span>
          </span>
          <span className="stat-label">채움말 빈도</span>
        </div>
        <div className="stat">
          <span className="stat-value">{silences}</span>
          <span className="stat-label">침묵 구간</span>
        </div>
      </div>

      <ul className="consistency-list">
        <li key={exampleIndex} className={`verdict-${example.verdict} report-preview-example`}>
          <div className="verdict-row">
            <span className="slide-tag">{example.slide}</span>
            <span className="verdict-badge">{example.label}</span>
          </div>
          <p className="claim">{example.claim}</p>
          <p className="evidence">{example.evidence}</p>
        </li>
      </ul>

      <p className="report-preview-hint">업로드하면 이런 리포트를 몇 분 안에 받아볼 수 있어요</p>
    </div>
  );
}
