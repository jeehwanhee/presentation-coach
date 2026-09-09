import { useCallback, useState } from "react";
import { uploadToPresignedUrl } from "../../api/presentations";

export type UploadStatus = "empty" | "uploading" | "done" | "error";

export interface FileUploadState {
  status: UploadStatus;
  fileName: string | null;
  errorMessage: string | null;
}

export function useFileUpload(uploadUrl: string) {
  const [state, setState] = useState<FileUploadState>({
    status: "empty",
    fileName: null,
    errorMessage: null,
  });

  const upload = useCallback(
    async (file: File) => {
      setState({ status: "uploading", fileName: file.name, errorMessage: null });
      try {
        await uploadToPresignedUrl(uploadUrl, file);
        setState({ status: "done", fileName: file.name, errorMessage: null });
      } catch {
        setState({
          status: "error",
          fileName: file.name,
          errorMessage: "업로드에 실패했어요. 다시 시도해주세요.",
        });
      }
    },
    [uploadUrl],
  );

  return { ...state, upload };
}

// 오디오 파일의 길이(ms)를 브라우저에서 직접 측정 — submit의 audio_duration_ms에 필요.
export function readAudioDurationMs(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const audio = document.createElement("audio");
    const url = URL.createObjectURL(file);
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      if (!Number.isFinite(audio.duration)) {
        reject(new Error("오디오 길이를 읽을 수 없어요."));
        return;
      }
      resolve(Math.round(audio.duration * 1000));
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("오디오 파일을 읽을 수 없어요."));
    };
    audio.src = url;
  });
}
