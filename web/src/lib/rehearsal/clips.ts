// Plays the pre-generated line audio through the Web Audio API.
//
// Why Web Audio rather than <audio> elements: phones only let a page make sound after a tap. One AudioContext,
// resumed by the Start button, stays unlocked for the whole rehearsal, so every later line can play by itself.

const RETRY_MS = 1000;
const MAX_TRIES = 90; // a clip that isn't made yet jumps the server's queue; 90 s covers a very long speech

export class ClipPlayer {
  private ctx: AudioContext | null = null;
  private cache = new Map<string, Promise<AudioBuffer>>();
  private current: AudioBufferSourceNode | null = null;
  private out: GainNode | null = null;

  /** `volume` 0 plays everything silently at normal speed: for testing the player without making a noise. */
  constructor(private volume = 1) {}

  /** Call from a tap handler, before anything is played. */
  async unlock(): Promise<void> {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.out = this.ctx.createGain();
      this.out.gain.value = this.volume;
      this.out.connect(this.ctx.destination);
    }
    if (this.ctx.state !== 'running') await this.ctx.resume();
  }

  private async fetchClip(hash: string): Promise<AudioBuffer> {
    for (let attempt = 0; attempt < MAX_TRIES; attempt++) {
      const r = await fetch(`/api/clips/${hash}`);
      if (r.status === 200) return await this.ctx!.decodeAudioData(await r.arrayBuffer());
      if (r.status !== 202) throw new Error(`clip ${r.status}`);
      await new Promise((resolve) => setTimeout(resolve, RETRY_MS)); // 202 = still being made
    }
    throw new Error('clip not ready in time');
  }

  load(hash: string): Promise<AudioBuffer> {
    let p = this.cache.get(hash);
    if (!p) {
      p = this.fetchClip(hash);
      p.catch(() => this.cache.delete(hash)); // let a later attempt try again
      this.cache.set(hash, p);
    }
    return p;
  }

  /** Start fetching clips that will be needed soon, so there is no gap when their turn comes. */
  preload(hashes: string[]): void {
    if (this.ctx) for (const h of hashes) this.load(h).catch(() => {});
  }

  /** Play one clip. Resolves when it finishes or is stopped. `onStart` fires when sound actually begins. */
  async play(hash: string, onStart?: () => void): Promise<void> {
    const buffer = await this.load(hash);
    const ctx = this.ctx!;
    this.stop();
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(this.out!);
    this.current = node;
    await new Promise<void>((resolve) => {
      node.onended = () => {
        if (this.current === node) this.current = null;
        resolve();
      };
      node.start();
      onStart?.();
    });
  }

  stop(): void {
    const node = this.current;
    this.current = null;
    try {
      node?.stop(); // fires onended, which resolves the pending play()
    } catch {
      // already stopped
    }
  }

  close(): void {
    this.stop();
    this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.out = null;
    this.cache.clear();
  }
}
