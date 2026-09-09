import { apiFetch } from "./client";
import type {
  CreatePresentationRequest,
  CreatePresentationResponse,
  GetPresentationResponse,
  SubmitPresentationResponse,
} from "../types/api";

export function createPresentation(
  body: CreatePresentationRequest,
): Promise<CreatePresentationResponse> {
  return apiFetch<CreatePresentationResponse>("/api/presentations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function submitPresentation(
  id: number,
  token: string,
  audioDurationMs: number,
): Promise<SubmitPresentationResponse> {
  return apiFetch<SubmitPresentationResponse>(`/api/presentations/${id}/submit`, {
    method: "POST",
    token,
    body: JSON.stringify({ audio_duration_ms: audioDurationMs }),
  });
}

export function getPresentation(id: number, token: string): Promise<GetPresentationResponse> {
  return apiFetch<GetPresentationResponse>(`/api/presentations/${id}`, {
    method: "GET",
    token,
  });
}

// presigned URL은 우리 API가 아니라 S3로 직접 쏘는 거라 apiFetch(BASE_URL 붙는 것) 안 씀.
export async function uploadToPresignedUrl(url: string, file: File | Blob): Promise<void> {
  const res = await fetch(url, { method: "PUT", body: file });
  if (!res.ok) {
    throw new Error(`파일 업로드 실패 (${res.status})`);
  }
}
