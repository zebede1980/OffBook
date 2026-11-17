<script lang="ts">
  // A diagnostic page: does the voice detector load and run on this device?
  // "Detector only" feeds it a generated tone, so it needs no microphone and makes no sound.
  // "With my microphone" shows the live speech reading, for checking a phone picks the child up.
  import { onDestroy } from 'svelte';
  import { Listener } from '#lib/rehearsal/listener';

  let state_ = $state<'idle' | 'loading' | 'running' | 'failed'>('idle');
  let frames = $state(0);
  let prob = $state(0);
  let peak = $state(0);
  let error = $state('');
  let seconds = $state(0);
  let ctx: AudioContext | null = null;
  let ear: Listener | null = null;
  let t0 = 0;

  async function run(useMic: boolean) {
    await stop();
    state_ = 'loading';
    error = '';
    frames = 0;
    peak = 0;
    try {
      ctx = new AudioContext();
      await ctx.resume();
      let getStream: (() => Promise<MediaStream>) | undefined;
      if (!useMic) {
        const tone = ctx.createOscillator();
        const sink = ctx.createMediaStreamDestination(); // a stream, not the speakers
        tone.connect(sink);
        tone.start();
        getStream = async () => sink.stream;
      }
      ear = new Listener();
      ear.onFrame = (p) => {
        if (!frames) t0 = performance.now();
        frames += 1;
        prob = p;
        peak = Math.max(peak, p);
        seconds = (performance.now() - t0) / 1000;
      };
      await ear.start(ctx, getStream);
      state_ = 'running';
    } catch (e) {
      error = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
      state_ = 'failed';
    }
  }

  async function stop() {
    await ear?.stop();
    ear = null;
    await ctx?.close().catch(() => {});
    ctx = null;
    if (state_ !== 'failed') state_ = 'idle';
  }

  onDestroy(stop);
</script>

<div class="page">
  <a class="small" href="/">‹ Home</a>
  <h1>Listening self-test</h1>
  <p class="muted">Checks that this device can run the part of OffBook that hears when a line is finished.</p>

  <div class="row">
    <button class="btn" onclick={() => run(false)} disabled={state_ === 'loading'}>Detector only</button>
    <button class="btn primary" onclick={() => run(true)} disabled={state_ === 'loading'}>With my microphone</button>
    {#if state_ === 'running'}<button class="btn ghost" onclick={stop}>Stop</button>{/if}
  </div>

  <div class="card out" aria-live="polite">
    {#if state_ === 'idle'}
      <p>Not running.</p>
    {:else if state_ === 'loading'}
      <p>Loading the detector (about 17 MB the first time)…</p>
    {:else if state_ === 'failed'}
      <p class="bad"><strong>Failed.</strong> {error}</p>
    {:else}
      <p id="result"><strong>Working.</strong> {frames} frames in {seconds.toFixed(1)} s.</p>
      <p>Speech right now: <strong>{(prob * 100).toFixed(0)}%</strong> (highest so far {(peak * 100).toFixed(0)}%)</p>
      <div class="meter"><span style:width="{prob * 100}%"></span></div>
      <p class="small muted">With the microphone on, talking should push this well over 50%, and quiet should drop it near 0.</p>
    {/if}
  </div>
</div>

<style>
  .row { flex-wrap: wrap; margin: 1rem 0; }
  .out { padding: 1rem; }
  .out p { margin: 0 0 0.5rem; }
  .bad { color: var(--danger); overflow-wrap: anywhere; }
  .meter { height: 14px; border-radius: 7px; background: var(--surface-2); overflow: hidden; margin: 0.5rem 0; }
  .meter span { display: block; height: 100%; background: var(--accent); transition: width 60ms linear; }
</style>
