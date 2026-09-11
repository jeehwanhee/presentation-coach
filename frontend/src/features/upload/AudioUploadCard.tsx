import type { ReactNode } from "react";
import { useRef, useState } from "react";
import type { SelectionStatus } from "./UploadCard";
import { AudioRecordPanel } from "./AudioRecordPanel";
import { formatFileSize } from "./formatFileSize";

interface AudioUploadCardProps {
  icon: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  status: SelectionStatus;
  fileName: string | null;
  fileSize: number | null;
  errorMessage: string | null;
  onSelect: (file: File) => void;
  onClear?: () => void;
}

export function AudioUploadCard({
  icon,
  title,
  hint,
  required = false,
  status,
  fileName,
  fileSize,
  errorMessage,
  onSelect,
  onClear,
}: AudioUploadCardProps) {
  const [mode, setMode] = useState<"file" | "record">("file");
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onSelect(file);
  }

  function handleDrop(e: React.DragEvent<HTMLButtonElement>) {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onSelect(file);
  }

  function handleClear() {
    if (inputRef.current) inputRef.current.value = "";
    onClear?.();
  }

  const isEmptyLike = status === "empty" || status === "error";
  const isDone = status === "selected" || status === "uploading";

  return (
    <div className={`upload-card status-${status === "selected" ? "done" : status}`}>
      <div className="card-head">
        <span className="card-icon">{icon}</span>
        <div>
          <p className="card-title">
            {title}
            {required && <span className="required-text card-required"> · 필수</span>}
          </p>
          {hint && <p className="card-hint">{hint}</p>}
        </div>
      </div>

      {isEmptyLike && (
        <div className="audio-mode-tabs">
          <button
            type="button"
            className={`audio-mode-tab ${mode === "file" ? "active" : ""}`}
            onClick={() => setMode("file")}
          >
            파일 선택
          </button>
          <button
            type="button"
            className={`audio-mode-tab ${mode === "record" ? "active" : ""}`}
            onClick={() => setMode("record")}
          >
            직접 녹음
          </button>
        </div>
      )}

      {isEmptyLike && mode === "file" && (
        <>
          <button
            type="button"
            className={`card-dropzone ${dragActive ? "drag-active" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
          >
            {status === "empty" && "클릭 또는 드래그해서 넣기"}
            {status === "error" && "다시 시도"}
          </button>
          <input ref={inputRef} type="file" accept="audio/*" hidden onChange={handleChange} />
        </>
      )}

      {isEmptyLike && mode === "record" && <AudioRecordPanel onRecorded={onSelect} />}

      {isDone && fileName && (
        <div className="file-chip">
          <span className="file-chip-icon">
            {status === "uploading" ? (
              <span className="file-chip-spinner" />
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            )}
          </span>
          <div className="file-chip-info">
            <span className="file-chip-name">{fileName}</span>
            {fileSize != null && <span className="file-chip-size">{formatFileSize(fileSize)}</span>}
          </div>
          {onClear && status !== "uploading" && (
            <button type="button" className="file-chip-remove" onClick={handleClear} aria-label="파일 제거">
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
