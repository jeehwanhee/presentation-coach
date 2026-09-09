// docs/API_명세서.md §2.3.1 / ai-service app/schemas/report.py 와 1:1 대응.

export type ConsistencyVerdict = "SUPPORTED" | "NOT_MENTIONED" | "NO_BASIS";

// 2026-09-07 확장판 — 11개 전부.
export type FillerType =
  | "음"
  | "어"
  | "그"
  | "저"
  | "뭐"
  | "뭔가"
  | "좀"
  | "막"
  | "그냥"
  | "같다"
  | "기타";

export type ScriptDiffKind = "생략" | "추가" | "변경";

export interface ConsistencyCheck {
  slide_index: number;
  claim: string;
  verdict: ConsistencyVerdict;
  evidence_span: string | null;
  evidence_at_ms: number | null;
}

export interface Consistency {
  checks: ConsistencyCheck[];
  supported_count: number;
  total_claims: number;
}

export interface OffTopicSegment {
  start_ms: number;
  end_ms: number;
  text: string;
  reason: string;
}

export interface LogicGap {
  at_ms: number;
  text: string;
  note: string;
}

export interface LongPause {
  start_ms: number;
  duration_ms: number;
}

export interface Filler {
  type: FillerType;
  text: string;
  at_ms: number;
  duration_ms: number;
}

export interface VolumeVariation {
  relative_std: number;
  note: string;
}

export interface Delivery {
  wpm: number;
  silence_total_ms: number;
  long_pauses: LongPause[];
  fillers: Filler[];
  filler_count: number;
  volume_variation: VolumeVariation;
}

export interface ScriptDeviation {
  at_ms: number;
  script_text: string;
  spoken_text: string;
  kind: ScriptDiffKind;
}

export interface ScriptDiff {
  matched_ratio: number;
  deviations: ScriptDeviation[];
}

export interface AnalysisReport {
  consistency: Consistency;
  off_topic: OffTopicSegment[];
  logic_gaps: LogicGap[];
  delivery: Delivery;
  script_diff: ScriptDiff | null;
}
