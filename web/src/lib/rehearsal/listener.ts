// The microphone, reduced to one number per frame: how likely it is that someone is speaking.
//
// The voice detector (Silero, via @ricky0123/vad-web) runs entirely in the browser. No audio leaves the device
// for this. The library and its 15 MB runtime are only loaded when listening is switched on.

/** `frame` is the raw 16 kHz audio of this frame: kept only if the child asked for recordings. */
export type FrameHandler = (prob: number, frameMs: number, frame: Float32Array) => void;

export class Listener {
  private vad: { start: () => Promise<void>; pause: () => Promise<void>; destroy: () => Promise<void> } | null = null;
  private last = 0;
  /** Set to receive frames; null to ignore the microphone (while the app itself is speaking). */
  onFrame: FrameHandler | null = null;

  /**
   * Ask for the microphone and start the detector. Throws if refused or unsupported.
   * Pass the audio context that the Start tap unlocked: one made here, after the permission prompt, would be
   * outside the tap and iOS would leave it suspended.
   */
  async start(audioContext: AudioContext, getStream?: () => Promise<MediaStream>): Promise<void> {
    if (!getStream && !navigator.mediaDevices?.getUserMedia) throw new Error('This browser has no microphone access.');
    const { MicVAD } = await import('@ricky0123/vad-web');
    this.vad = await MicVAD.new({
      model: 'v5',
      baseAssetPath: '/vad/',
      onnxWASMBasePath: '/vad/',
      startOnLoad: false,
      audioContext,
      // echo cancellation matters: the phone's speaker is inches from its microphone
      getStream:
        getStream ??
        (() =>
          navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
          })),
      onFrameProcessed: (probs, frame) => {
        const now = performance.now();
        // measure the frame length rather than assume it; clamp so a stalled tab can't count as a long silence
        const ms = this.last ? Math.min(100, Math.max(10, now - this.last)) : 32;
        this.last = now;
        this.onFrame?.(probs.isSpeech, ms, frame);
      }
    });
    await this.vad.start();
  }

  async stop(): Promise<void> {
    this.onFrame = null;
    const vad = this.vad;
    this.vad = null;
    await vad?.destroy().catch(() => {});
  }
}
