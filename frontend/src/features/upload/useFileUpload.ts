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
