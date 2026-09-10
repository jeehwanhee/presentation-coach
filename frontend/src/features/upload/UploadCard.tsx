import type { ReactNode } from "react";
import { useRef } from "react";

export type SelectionStatus = "empty" | "selected" | "uploading" | "error";

interface UploadCardProps {
  num: number;
  title: ReactNode;
  hint?: ReactNode;
  accept: string;
  status: SelectionStatus;
  fileName: string | null;
  errorMessage: string | null;
  onSelect: (file: File) => void;
  onClear?: () => void;
  required?: boolean;
}

export function UploadCard({
  num,
  title,
  hint,
  accept,
  status,
  fileName,
  errorMessage,
  onSelect,
  onClear,
  required = false,
}: UploadCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onSelect(file);
  }

  function handleClear() {
    if (inputRef.current) inputRef.current.value = "";
    onClear?.();
  }

  return (
    <div className={`upload-card status-${status === "selected" ? "done" : status}`}>
      <span className="num-badge">{num}</span>
      <p className="card-title">
        {title}
        {required && <span className="required-text card-required"> · 필수</span>}
      </p>
      {hint && <p className="card-hint">{hint}</p>}

      <button
        type="button"
        className="card-dropzone"
        onClick={() => inputRef.current?.click()}
        disabled={status === "uploading"}
      >
        {status === "empty" && "클릭 또는 드래그해서 넣기"}
        {status === "uploading" && "업로드 중..."}
        {status === "selected" && "✓ 선택 완료"}
        {status === "error" && "다시 시도"}
      </button>

      <input ref={inputRef} type="file" accept={accept} hidden onChange={handleChange} />

      {fileName && (
        <div className="card-file-row">
          <span className="card-file">{fileName}</span>
          {onClear && status !== "uploading" && (
            <button
              type="button"
              className="card-file-remove"
              onClick={handleClear}
              aria-label="파일 제거"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      )}
      {status === "error" && errorMessage && <p className="error-text">{errorMessage}</p>}
    </div>
  );
}
