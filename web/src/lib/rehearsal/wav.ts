// Turns microphone frames (float samples, -1..1) into a 16-bit mono PCM WAV file, the one format the server
// accepts for recordings.

export function encodeWav(frames: Float32Array[], sampleRate = 16000): Blob {
  const samples = frames.reduce((n, f) => n + f.length, 0);
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const text = (at: number, s: string) => [...s].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  text(0, 'RIFF');
  v.setUint32(4, 36 + samples * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  v.setUint32(16, 16, true); // fmt chunk size
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); // bytes per second
  v.setUint16(32, 2, true); // bytes per sample
  v.setUint16(34, 16, true); // bits per sample
  text(36, 'data');
  v.setUint32(40, samples * 2, true);
  let at = 44;
  for (const f of frames) {
    for (let i = 0; i < f.length; i++, at += 2) {
      const x = Math.max(-1, Math.min(1, f[i]));
      v.setInt16(at, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
  }
  return new Blob([buf], { type: 'audio/wav' });
}
