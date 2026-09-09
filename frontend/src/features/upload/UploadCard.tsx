import { useRef } from "react";
import type { UploadStatus } from "./useFileUpload";

interface UploadCardProps {
  num: number;
  title: string;
  accept: string;
  status: UploadStatus;
  fileName: string | null;
  errorMessage: string | null;
  onSelect: (file: File) => void;
}

export function UploadCard({ num, title, accept, status, fileName, errorMessage, onSelect }: UploadCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onSelect(file);
  }

  return (
    <div className={`upload-card status-${status}`}>
      <span className="num-badge">{num}</span>
      <p className="card-title">{title}</p>

      <button
        type="button"
        className="card-dropzone"
        onClick={() => inputRef.current?.click()}
        disabled={status === "uploading"}
      >
        {status === "empty" && "클릭 또는 드래그해서 넣기"}
        {status === "uploading" && "업로드 중..."}
        {status === "done" && "✓ 업로드 완료"}
        {status === "error" && "다시 시도"}
      </button>

      <input ref={inputRef} type="file" accept={accept} hidden onChange={handleChange} />

      {fileName && <p className="card-file">{fileName}</p>}
      {status === "error" && errorMessage && <p className="error-text">{errorMessage}</p>}
    </div>
  );
}
