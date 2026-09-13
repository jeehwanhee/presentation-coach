import type { Delivery, VolumeVariation } from "../../types/report";

type Tone = "good" | "mid" | "bad";

interface CardContent {
  title: string;
  valuePrefix?: string;
  valueText: string;
  tone: Tone | null; // null = 등급 없음(정보성 카드)
  label: string | null;
  description: string;
  descriptionNote?: string;
  tip: string;
}

function formatMs(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}분 ${seconds}초`;
}

/**
 * 발표 코칭 자료 기준 참고값 (2026-09 조사):
 * - WPM 120~160이 적정 구간, 100 미만은 청중 이탈, 180 초과는 이해도 저하
 *   (National Communication Association 기준 인용, virtualspeech.com 등 다수 출처)
 * - 채움말: 평균 화자 분당 5회, 전문 발표자 분당 1~2회, 분당 10회 넘으면 확실히 거슬림
 *   (Quantified Communications, Carleton Univ. 커뮤니케이션 연구)
 * - "긴 침묵"(long_pauses)은 ai-service가 1초(LONG_PAUSE_THRESHOLD_MS) 이상 공백을
 *   기준으로 잡음 — TED의 "분당 5회 전략적 멈춤"(의도된 임팩트용) 통계와는 성격이
 *   달라 그대로 못 쓰고 보수적으로 별도 기준을 잡음.
 * - 침묵 비율은 리서치에서 직접적인 기준을 못 찾아 일반적인 판단으로 잡음.
 * 전부 "일반적인 가이드라인" 수준 참고값이라 발표 주제·상황에 따라 다를 수 있음.
 */

function wpmCard(wpm: number): CardContent {
  const rounded = Math.round(wpm);
  const description = "1분 동안 말한 단어 수";

  if (wpm >= 120 && wpm <= 160) {
    return {
      title: "분당 단어 수(WPM)",
      valueText: `${rounded}`,
      tone: "good",
      label: "적절",
      description,
      tip: "지금 속도를 그대로 유지하면 돼요.",
    };
  }
  if (wpm < 120) {
    const tone: Tone = wpm >= 100 ? "mid" : "bad";
    return {
      title: "분당 단어 수(WPM)",
      valueText: `${rounded}`,
      tone,
      label: tone === "mid" ? "보통" : "개선 필요",
      description,
      tip: "속도가 느린 편이에요. 문장 사이 불필요한 머뭇거림을 줄이고, 핵심 위주로 리듬감 있게 말해보세요.",
    };
  }
  const tone: Tone = wpm <= 180 ? "mid" : "bad";
  return {
    title: "분당 단어 수(WPM)",
    valueText: `${rounded}`,
    tone,
    label: tone === "mid" ? "보통" : "개선 필요",
    description,
    tip: "속도가 빠른 편이에요. 문장 사이에 짧게 숨을 고르면서 청중이 따라올 시간을 주세요.",
  };
}

function fillerCard(fillerCount: number, durationMin: number | null): CardContent {
  const description = "음, 어, 그 같은 채움말을 사용한 횟수";
  const descriptionNote = "(발표 길이 대비 분당 기준)";

  if (!durationMin) {
    return { title: "채움말 횟수", valueText: `${fillerCount}`, tone: null, label: null, description, descriptionNote, tip: "" };
  }

  const perMin = fillerCount / durationMin;
  if (perMin <= 2) {
    return {
      title: "채움말 횟수",
      valueText: `${fillerCount}`,
      tone: "good",
      label: "적절",
      description,
      descriptionNote,
      tip: "채움말 사용이 적어서 깔끔하게 전달되고 있어요.",
    };
  }
  if (perMin <= 5) {
    return {
      title: "채움말 횟수",
      valueText: `${fillerCount}`,
      tone: "mid",
      label: "보통",
      description,
      descriptionNote,
      tip: "채움말이 조금 있어요. 막힐 때 채움말 대신 짧게 침묵하는 연습을 해보세요.",
    };
  }
  return {
    title: "채움말 횟수",
    valueText: `${fillerCount}`,
    tone: "bad",
    label: "개선 필요",
    description,
    descriptionNote,
    tip: "채움말이 잦아요. 침묵을 두려워하지 말고, 다음 문장을 떠올리는 동안엔 그냥 잠깐 멈춰보세요.",
  };
}

function silenceCard(silenceMs: number, audioDurationMs: number | null): CardContent {
  const description = "발표 전체에서 말을 멈춘 시간의 합";
  const descriptionNote = "(발표 길이 대비 비율로 판단)";

  if (!audioDurationMs) {
    return { title: "총 침묵 시간", valueText: formatMs(silenceMs), tone: null, label: null, description, descriptionNote, tip: "" };
  }

  const ratio = silenceMs / audioDurationMs;
  if (ratio <= 0.15) {
    return {
      title: "총 침묵 시간",
      valueText: formatMs(silenceMs),
      tone: "good",
      label: "적절",
      description,
      descriptionNote,
      tip: "침묵을 적절히 활용하고 있어요.",
    };
  }
  if (ratio <= 0.25) {
    return {
      title: "총 침묵 시간",
      valueText: formatMs(silenceMs),
      tone: "mid",
      label: "보통",
      description,
      descriptionNote,
      tip: "침묵이 조금 많은 편이에요. 준비한 내용을 조금 더 자연스럽게 이어가보세요.",
    };
  }
  return {
    title: "총 침묵 시간",
    valueText: formatMs(silenceMs),
    tone: "bad",
    label: "개선 필요",
    description,
    descriptionNote,
    tip: "침묵이 많아요. 대본을 좀 더 숙지하거나, 다음 내용을 미리 떠올리는 연습을 하면 끊김이 줄어들어요.",
  };
}

function longPauseCard(count: number, durationMin: number | null): CardContent {
  const description = "1초 이상 길게 멈춘 구간의 개수";
  const descriptionNote = "(발표 길이 대비 분당 기준)";

  if (!durationMin) {
    return { title: "긴 침묵 구간", valueText: `${count}개`, tone: null, label: null, description, descriptionNote, tip: "" };
  }

  const perMin = count / durationMin;
  if (perMin <= 1) {
    return {
      title: "긴 침묵 구간",
      valueText: `${count}개`,
      tone: "good",
      label: "적절",
      description,
      descriptionNote,
      tip: "긴 침묵 없이 매끄럽게 이어갔어요.",
    };
  }
  if (perMin <= 2) {
    return {
      title: "긴 침묵 구간",
      valueText: `${count}개`,
      tone: "mid",
      label: "보통",
      description,
      descriptionNote,
      tip: "긴 침묵이 몇 번 있었어요. 리포트의 침묵 구간 타임스탬프를 보고 어디서 막혔는지 확인해보세요.",
    };
  }
  return {
    title: "긴 침묵 구간",
    valueText: `${count}개`,
    tone: "bad",
    label: "개선 필요",
    description,
    descriptionNote,
    tip: "긴 침묵이 잦아요. 막히는 구간을 미리 파악해서 그 부분만 집중적으로 연습해보세요.",
  };
}

function volumeCard(volumeVariation: VolumeVariation): CardContent {
  return {
    title: "성량 기복",
    valuePrefix: "200ms 프레임 RMS 기준",
    valueText: `변동계수 ${volumeVariation.relative_std.toFixed(2)}`,
    tone: null,
    label: null,
    description: "말하는 동안 목소리 크기가 얼마나 변하는지 나타내는 값",
    descriptionNote: "(값이 클수록 성량 기복이 큼)",
    tip: "아직 실제 데이터가 부족해 적절/보통/개선 필요 등급은 제공하지 않아요.",
  };
}

export interface DeliveryRating {
  metric: string;
  value: string;
  tone: Tone;
  label: string;
}

export function getDeliveryRatings(delivery: Delivery, audioDurationMs: number | null): DeliveryRating[] {
  const durationMin = audioDurationMs ? audioDurationMs / 60_000 : null;
  const cards = [
    wpmCard(delivery.wpm),
    fillerCard(delivery.filler_count, durationMin),
    silenceCard(delivery.silence_total_ms, audioDurationMs),
    longPauseCard(delivery.long_pauses.length, durationMin),
  ];
  return cards
    .filter((c) => c.tone !== null && c.label !== null)
    .map((c) => ({ metric: c.title, value: c.valueText, tone: c.tone as Tone, label: c.label as string }));
}

function Card({ card }: { card: CardContent }) {
  return (
    <div className="delivery-card">
      <div className="delivery-card-head">
        <span className="delivery-card-title">{card.title}</span>
        {card.tone && card.label && <span className={`stat-rating tone-${card.tone}`}>{card.label}</span>}
      </div>
      {card.valuePrefix && <span className="delivery-card-value-prefix">{card.valuePrefix}</span>}
      {card.valueText && <span className="delivery-card-value">{card.valueText}</span>}
      <p className="delivery-card-desc">
        {card.description}
        {card.descriptionNote && (
          <>
            <br />
            {card.descriptionNote}
          </>
        )}
      </p>
      <p className={`delivery-card-tip ${card.tone ? `tone-${card.tone}` : ""}`}>{card.tip}</p>
    </div>
  );
}

interface DeliverySectionProps {
  delivery: Delivery;
  audioDurationMs: number | null;
}

export function DeliverySection({ delivery, audioDurationMs }: DeliverySectionProps) {
  const durationMin = audioDurationMs ? audioDurationMs / 60_000 : null;

  const wpm = wpmCard(delivery.wpm);
  const fillers = fillerCard(delivery.filler_count, durationMin);
  const silence = silenceCard(delivery.silence_total_ms, audioDurationMs);
  const longPauses = longPauseCard(delivery.long_pauses.length, durationMin);
  const volume = volumeCard(delivery.volume_variation);

  return (
    <section className="report-section">
      <h3>전달력</h3>
      <div className="delivery-cards">
        <Card card={wpm} />
        <Card card={fillers} />
        <Card card={silence} />
        <Card card={longPauses} />
        <Card card={volume} />
      </div>
      <ul className="notice-list">
        <li>
          <span className="notice-dot" />
          발표 길이를 반영해 분당 비율로 평가했어요. 일반적인 발표 가이드라인 기준 참고값이라
          주제·상황에 따라 실제 적정 수준은 다를 수 있어요.
        </li>
        <li>
          <span className="notice-dot" />
          이 리포트는 생성 후 3일이 지나면 자동으로 사라져요. 다시 보려면 위 링크를 꼭 저장해두세요.
        </li>
      </ul>
    </section>
  );
}
