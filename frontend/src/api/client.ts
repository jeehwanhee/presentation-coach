import type { ErrorResponse } from "../types/api";

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL;

if (!BASE_URL) {
  // 개발 중 .env 설정을 깜빡했을 때 바로 알아채기 위함.
  console.error("VITE_API_BASE_URL이 설정되지 않았습니다 (.env 확인)");
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, code: string | undefined, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

interface ApiFetchOptions extends RequestInit {
  token?: string;
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { token, headers, ...rest } = options;

  const res = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    headers: {
      ...(rest.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { "X-Result-Token": token } : {}),
      ...headers,
    },
  });

  if (!res.ok) {
    let body: ErrorResponse | null = null;
    try {
      body = await res.json();
    } catch {
      // 에러 응답이 JSON이 아닐 수도 있음(예: 프록시 단계 에러) — 그냥 상태 코드만으로 처리.
    }
    throw new ApiError(
      res.status,
      body?.error?.code,
      body?.error?.message ?? `요청이 실패했습니다 (${res.status})`,
    );
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}
