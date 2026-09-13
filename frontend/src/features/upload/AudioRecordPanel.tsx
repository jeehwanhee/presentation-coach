import { useAudioRecorder } from "./useAudioRecorder";

function formatTimer(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

interface AudioRecordPanelProps {
  onRecorded: (file: File) => void;
}

export function AudioRecordPanel({ onRecorded }: AudioRecordPanelProps) {
  const { status, elapsedMs, error, start, stop } = useAudioRecorder();

  async function handleClick() {
    if (status === "recording") {
      const file = await stop();
      if (file) onRecorded(file);
    } else {
      start();
    }
  }

  return (
    <div className="record-panel">
      <button
        type="button"
        className={`record-btn ${status === "recording" ? "recording" : ""}`}
        onClick={handleClick}
        disabled={status === "requesting"}
      >
        <span className={`record-dot ${status === "recording" ? "square" : ""}`} />
        {status === "recording" && `녹음 중지 · ${formatTimer(elapsedMs)}`}
        {status === "requesting" && "마이크 준비 중..."}
        {(status === "idle" || status === "error") && "녹음 시작"}
      </button>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
