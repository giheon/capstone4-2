// Client-side probe of a chosen video: duration, size and a thumbnail frame.
// The mock backend does not receive the file; the thumbnail is kept per case for display only.

export interface ProbeResult {
  durationSec?: number;
  width?: number;
  height?: number;
  thumbnail?: string;
  error?: string;
}

export function probeVideo(file: File): Promise<ProbeResult> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    let settled = false;
    const finish = (result: ProbeResult) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
      resolve(result);
    };
    const timer = window.setTimeout(() => finish({ error: "미리보기를 만들지 못했습니다." }), 8000);
    video.preload = "metadata";
    video.muted = true;
    video.playsInline = true;
    video.onloadedmetadata = () => {
      const duration = Number.isFinite(video.duration) ? video.duration : undefined;
      if (!video.videoWidth) {
        finish({ durationSec: duration, error: "이 브라우저가 이 영상의 코덱을 재생하지 못해 썸네일을 만들 수 없습니다." });
        return;
      }
      video.currentTime = Math.min(1, (duration ?? 2) / 3);
    };
    video.onseeked = () => {
      const width = video.videoWidth;
      const height = video.videoHeight;
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = Math.round((320 * height) / width);
      const ctx = canvas.getContext("2d");
      let thumbnail: string | undefined;
      if (ctx) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        thumbnail = canvas.toDataURL("image/jpeg", 0.72);
      }
      finish({ durationSec: video.duration, width, height, thumbnail });
    };
    video.onerror = () => finish({ error: "이 브라우저에서 읽을 수 없는 영상 형식입니다." });
    video.src = url;
  });
}

const KEY = "privframe:thumbs";

export function rememberThumbnail(caseId: string, dataUrl: string): void {
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Record<string, string>;
    stored[caseId] = dataUrl;
    sessionStorage.setItem(KEY, JSON.stringify(stored));
  } catch {
    /* storage may be unavailable; the thumbnail is optional */
  }
}

export function recallThumbnail(caseId: string): string | undefined {
  try {
    return (JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Record<string, string>)[caseId];
  } catch {
    return undefined;
  }
}
