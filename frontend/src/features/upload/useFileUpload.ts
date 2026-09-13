// 오디오 파일의 길이(ms)를 브라우저에서 직접 측정 — submit의 audio_duration_ms에 필요.
export function readAudioDurationMs(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const audio = document.createElement("audio");
    const url = URL.createObjectURL(file);
    audio.preload = "metadata";

    function finish(durationSec: number) {
      URL.revokeObjectURL(url);
      resolve(Math.round(durationSec * 1000));
    }

    function fail() {
      URL.revokeObjectURL(url);
      reject(new Error("오디오 길이를 읽을 수 없어요."));
    }

    audio.onloadedmetadata = () => {
      if (Number.isFinite(audio.duration)) {
        finish(audio.duration);
        return;
      }
      // Chrome에서 MediaRecorder로 녹음한 webm blob은 duration이 Infinity로 뜨는 알려진
      // 문제가 있음 — 끝까지 seek했다가 되돌리면 그제서야 duration이 채워짐.
      audio.ontimeupdate = () => {
        audio.ontimeupdate = null;
        if (Number.isFinite(audio.duration)) {
          finish(audio.duration);
        } else {
          fail();
        }
      };
      audio.currentTime = Number.MAX_SAFE_INTEGER;
    };
    audio.onerror = fail;
    audio.src = url;
  });
}
