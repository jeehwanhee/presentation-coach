import type { AnalysisReport } from "./report";

// docs/API_명세서.md §1 error.code
export type ErrorCode =
  | "STT_FAILED"
  | "PPTX_PARSE_FAILED"
  | "AUDIO_NOT_FOUND"
  | "LLM_FAILED"
  | "TIMEOUT"
  | "VALIDATION_ERROR"
  | "AUDIO_DURATION_EXCEEDED"
  | "PRESENTATION_NOT_FOUND"
  | "PRESENTATION_EXPIRED"
  | "MISSING_HEADER"
  | "INTERNAL_ERROR"
  | string;

export interface ErrorBody {
  code: ErrorCode;
  message: string;
}

export interface ErrorResponse {
  error: ErrorBody;
}

// §2.1
export interface CreatePresentationRequest {
  title: string;
  script: string | null;
}

export interface CreatePresentationResponse {
  presentation_id: number;
  slide_upload_url: string;
  slide_s3_key: string;
  audio_upload_url: string;
  audio_s3_key: string;
  result_token: string;
  result_url: string;
  expires_at: string;
}

// §2.2
export interface SubmitPresentationRequest {
  audio_duration_ms: number;
}

export type PresentationStatus = "PENDING" | "PROCESSING" | "DONE" | "FAILED";

export interface SubmitPresentationResponse {
  presentation_id: number;
  status: PresentationStatus;
}

// §2.3 — report/error는 상태에 따라 널일 수 있음(필드 자체는 항상 존재)
export interface GetPresentationResponse {
  presentation_id: number;
  status: PresentationStatus;
  report: AnalysisReport | null;
  error: ErrorBody | null;
}
