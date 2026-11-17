<script lang="ts">
  // Shows how many of a script's lines have their voice ready, and keeps checking while some are being made.
  // Making voices takes minutes for a long script, so this is what tells the kids why a rehearsal might pause.
  import { onDestroy, onMount } from 'svelte';
  import { api } from '#lib/api';
  import { errorToast } from '#lib/toast.svelte';
  import type { AudioProgress } from '#lib/types';

  let {
    scriptId,
    progress = $bindable(null),
    compact = false,
    every = 4000
  }: { scriptId: number; progress?: AudioProgress | null; compact?: boolean; every?: number } = $props();

  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  async function poll() {
    clearTimeout(timer);
    try {
      progress = await api.get<AudioProgress>(`/api/scripts/${scriptId}/audio`);
    } catch {
      // a missed check is not worth an error; the next one will do
    }
    if (!stopped && progress && (progress.pending > 0 || progress.reading_pages > 0)) timer = setTimeout(poll, every);
  }

  /** Call after something that queues new audio (an edit, a voice change), to start watching again. */
  export function refresh() {
    poll();
  }

  async function retry() {
    try {
      progress = await api.post<AudioProgress>(`/api/scripts/${scriptId}/audio/retry`);
      poll();
    } catch (e) {
      errorToast(e);
    }
  }

  onMount(poll);
  onDestroy(() => {
    stopped = true;
    clearTimeout(timer);
  });

  function eta(s: number): string {
    if (s < 50) return 'under a minute';
    const m = Math.round(s / 60);
    return m === 1 ? 'about a minute' : `about ${m} minutes`;
  }

  const pct = $derived(progress && progress.total ? Math.round((progress.ready / progress.total) * 100) : 0);
  const busy = $derived(!!progress && progress.pending > 0);
</script>

{#if progress && (busy || progress.failed > 0)}
  <div class="vp" class:compact role="status" aria-live="polite">
    {#if busy}
      <div class="row">
        <span class="dot" aria-hidden="true"></span>
        {#if compact}
          <span>Voices {pct}%</span>
        {:else}
          <span><strong>Making voices:</strong> {progress.ready} of {progress.total} lines ready, {eta(progress.eta_seconds)} to go.</span>
        {/if}
      </div>
      <div class="bar"><span style:width="{pct}%"></span></div>
      {#if !compact}
        <p class="small muted">You can rehearse now. It may pause for a few seconds before a line whose voice isn't made yet.</p>
      {/if}
    {/if}
    {#if progress.failed > 0 && !compact}
      <p class="small warn">
        {progress.failed} line{progress.failed === 1 ? '' : 's'} couldn't be voiced.
        <button class="btn small" onclick={retry}>Try again</button>
      </p>
    {/if}
  </div>
{/if}

<style>
  .vp { background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px; padding: 0.6rem 0.8rem; margin: 0.5rem 0; }
  .vp.compact { padding: 0.2rem 0.5rem; margin: 0; border-radius: 8px; font-size: 0.8rem; min-width: 7.5rem; }
  .vp p { margin: 0.4rem 0 0; }
  .row { display: flex; gap: 0.5rem; align-items: center; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--accent); flex: none; animation: blink 1.2s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: 0.25; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
  .bar { height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; margin-top: 0.4rem; }
  .compact .bar { height: 4px; margin-top: 0.2rem; }
  .bar span { display: block; height: 100%; background: var(--accent); transition: width 400ms ease; }
  .warn { color: var(--danger); }
</style>
