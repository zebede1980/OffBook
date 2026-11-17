<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '#lib/api';
  import { ClipPlayer } from '#lib/rehearsal/clips';
  import { countWords, newState, step, type Verdict } from '#lib/rehearsal/endpoint';
  import { Listener } from '#lib/rehearsal/listener';
  import { session } from '#lib/session.svelte';
  import { castHue } from '#lib/text';
  import { errorToast, toast } from '#lib/toast.svelte';
  import type { Rehearsal, RehearsalLine } from '#lib/types';

  type View = 'script' | 'cues' | 'offbook';
  const id = Number(page.params.id);
  // ?quiet=1 runs the whole rehearsal silently (testing late at night)
  const player = new ClipPlayer(page.url.searchParams.has('quiet') ? 0 : 1);
  const HINT_STEP = 3;
  const views: { id: View; label: string; blurb: string }[] = [
    { id: 'script', label: 'Script', blurb: 'See every line as it is read.' },
    { id: 'cues', label: 'Cues', blurb: "See the others' lines. Yours stay hidden." },
    { id: 'offbook', label: 'Off book', blurb: 'No script at all. Just listen, and say your lines.' }
  ];

  let data = $state<Rehearsal | null>(null);
  let phase = $state<'setup' | 'running' | 'done'>('setup');
  let view = $state<View>('script');
  let readMine = $state(false);
  let startAt = $state(0);
  let oneScene = $state(false);
  let listen = $state(false);

  let seq = $state<RehearsalLine[]>([]);
  let idx = $state(0);
  let status = $state<'loading' | 'speaking' | 'yourturn' | 'paused'>('loading');
  let hint = $state(0);

  // Each run() gets a number. Anything that restarts playback bumps it, and the old loop sees the change and stops.
  let gen = 0;
  let advance: (() => void) | null = null;
  let wake: WakeLockSentinel | null = null;
  let ear: Listener | null = null;
  // what the microphone makes of the current turn; 'off' when not listening
  let hear = $state<Verdict['kind'] | 'off'>('off');
  let progress = $state(0);

  const cast = $derived(new Map((data?.characters ?? []).map((c, i) => [c.character_id, { ...c, hue: castHue(i) }])));
  const scenes = $derived((data?.lines ?? []).map((l, i) => ({ l, i })).filter((x) => x.l.kind === 'heading'));
  const current = $derived(seq[idx] ?? null);
  const myLineCount = $derived((data?.lines ?? []).filter((l) => l.mine).length);

  function who(l: RehearsalLine): string {
    return l.speakers.map((c) => cast.get(c)?.name ?? '?').join(' & ') || '?';
  }

  function hintText(l: RehearsalLine): string {
    const words = (l.spoken ?? l.text).split(/\s+/).filter(Boolean);
    return words.slice(0, hint).join(' ') + (hint < words.length ? ' …' : '');
  }

  async function load() {
    try {
      data = await api.get<Rehearsal>(`/api/scripts/${id}/rehearsal`);
    } catch (e) {
      errorToast(e);
    }
  }

  onMount(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('offbook.rehearse') ?? '{}');
      if (views.some((v) => v.id === saved.view)) view = saved.view;
      listen = saved.listen === true;
    } catch {
      // no saved preference; the defaults are fine
    }
    load();
    document.addEventListener('visibilitychange', onVisible);
  });

  onDestroy(() => {
    gen++;
    ear?.stop();
    player.close();
    wake?.release().catch(() => {});
    document.removeEventListener('visibilitychange', onVisible);
  });

  // the screen lock is dropped whenever the tab is hidden, so ask again on return
  function onVisible() {
    if (document.visibilityState === 'visible' && phase === 'running') keepAwake();
  }

  async function keepAwake() {
    try {
      wake = (await navigator.wakeLock?.request('screen')) ?? null;
    } catch {
      // not supported, or refused (low battery): rehearsal still works, the screen may just dim
    }
  }

  async function togglePart(cid: number) {
    if (!data) return;
    const next = data.mine.includes(cid) ? data.mine.filter((x) => x !== cid) : [...data.mine, cid];
    try {
      await api.put(`/api/scripts/${id}/casting`, { character_ids: next });
      await load();
    } catch (e) {
      errorToast(e);
    }
  }

  async function start() {
    if (!data) return;
    let end = data.lines.length;
    if (oneScene) {
      // the scene ends at the next heading after its first speech (a title and a scene heading can sit together)
      const firstSpeech = data.lines.findIndex((l, i) => i >= startAt && l.kind === 'dialogue');
      const next = firstSpeech < 0 ? -1 : data.lines.findIndex((l, i) => i > firstSpeech && l.kind === 'heading');
      if (next > 0) end = next;
    }
    seq = data.lines.slice(startAt, end);
    try {
      localStorage.setItem('offbook.rehearse', JSON.stringify({ view, listen }));
    } catch {
      // private browsing: nothing to remember, nothing lost
    }
    await begin();
  }

  /** Start (or restart) playing `seq` from the top. Must be called from a tap. */
  async function begin() {
    await player.unlock(); // must happen inside the tap, or phones stay silent
    if (listen && data?.mine.length && !readMine && !ear) {
      // Not awaited: the permission prompt can sit unanswered, and the scene should start regardless.
      // Until the microphone is ready, turns simply wait for a tap.
      const l = new Listener();
      l.start(player.context!)
        .then(() => {
          if (phase === 'running') ear = l;
          else l.stop();
        })
        .catch(() => toast("Couldn't use the microphone, so tap when you've said your line."));
    }
    phase = 'running';
    keepAwake();
    run(0);
  }

  function deaf() {
    if (ear) ear.onFrame = null;
    hear = 'off';
    progress = 0;
  }

  const waitForTap = () => new Promise<void>((resolve) => (advance = resolve));

  function release() {
    const a = advance;
    advance = null;
    a?.();
  }

  function upcoming(from: number): string[] {
    const out: string[] = [];
    for (let i = from; i < seq.length && out.length < 4; i++) {
      const l = seq[i];
      if (l.clip && (!l.mine || readMine)) out.push(l.clip);
    }
    return out;
  }

  async function run(from: number) {
    const my = ++gen;
    player.stop();
    release();
    for (let i = from; i < seq.length; i++) {
      if (my !== gen) return;
      const l = seq[i];
      idx = i;
      hint = 0;
      if (l.kind !== 'dialogue') continue; // headings and directions are shown, never spoken
      player.preload(upcoming(i));
      if (l.clip && (!l.mine || readMine)) {
        status = 'loading';
        try {
          await player.play(l.clip, () => {
            if (my === gen) status = 'speaking';
          });
        } catch {
          if (my !== gen) return;
          toast("That line's voice isn't ready. Tap to carry on.");
          status = 'yourturn';
          await waitForTap();
        }
      } else {
        status = 'yourturn';
        if (ear && l.mine) {
          const turn = newState();
          const words = countWords(l.spoken ?? l.text);
          const patience = session.profile?.patience ?? 1;
          hear = 'waiting';
          ear.onFrame = (prob, ms) => {
            const v = step(turn, prob, ms, words, patience);
            // ~30 frames a second arrive; only touch the screen when something visible changes
            if (v.kind !== hear) hear = v.kind;
            const p = v.kind === 'pausing' ? Math.round(v.progress * 20) / 20 : 0;
            if (p !== progress) progress = p;
            if (v.kind === 'advance') release();
          };
        }
        await waitForTap();
        // a restart (Back, Pause) has already set up the next turn's listener: leave it alone
        if (my !== gen) return;
        deaf();
      }
    }
    if (my === gen) {
      phase = 'done';
      wake?.release().catch(() => {});
      ear?.stop();
      ear = null;
    }
  }

  function next() {
    if (status === 'paused') run(idx);
    else if (status === 'yourturn') release();
    else if (status === 'speaking') player.stop(); // skip the rest of this line
  }

  function back() {
    for (let j = idx - 1; j >= 0; j--) {
      if (seq[j].kind === 'dialogue') {
        run(j);
        return;
      }
    }
    run(0);
  }

  function pause() {
    gen++;
    player.stop();
    release();
    deaf();
    status = 'paused';
  }

  function leave() {
    gen++;
    player.stop();
    release();
    deaf();
    ear?.stop();
    ear = null;
    wake?.release().catch(() => {});
    phase = 'setup';
    load();
  }

  $effect(() => {
    if (phase === 'running' && view !== 'offbook' && current) {
      document.getElementById(`l${current.line_id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  });

  const myTurn = $derived(status === 'yourturn' && !!current?.mine);
</script>

{#if data}
  {#if phase === 'setup'}
    <div class="page">
      <a class="small" href="/script/{id}">‹ {data.script.title}</a>
      <h1>Rehearse</h1>

      <section>
        <h2>Who are you playing?</h2>
        <div class="chips">
          {#each data.characters as c (c.character_id)}
            <button class="chip" style:--h={cast.get(c.character_id)?.hue} aria-pressed={data.mine.includes(c.character_id)} class:on={data.mine.includes(c.character_id)} onclick={() => togglePart(c.character_id)}>{c.name}</button>
          {/each}
        </div>
        <p class="small muted">
          {#if data.mine.length}You have {myLineCount} line{myLineCount === 1 ? '' : 's'}.{:else}Pick your part, or just listen to the whole thing.{/if}
        </p>
      </section>

      <section>
        <h2>How do you want to see it?</h2>
        <div class="viewpick" role="radiogroup" aria-label="How to see the script">
          {#each views as v}
            <button role="radio" aria-checked={view === v.id} class:on={view === v.id} onclick={() => (view = v.id)}>
              <strong>{v.label}</strong><span class="small">{v.blurb}</span>
            </button>
          {/each}
        </div>
      </section>

      <section>
        <h2>Where from?</h2>
        <select aria-label="Start from" bind:value={startAt}>
          <option value={0}>The beginning</option>
          {#each scenes as s}{#if s.i > 0}<option value={s.i}>{s.l.text}</option>{/if}{/each}
        </select>
        <label class="tick"><input type="checkbox" bind:checked={oneScene} /> Stop at the end of that scene</label>
        <label class="tick"><input type="checkbox" bind:checked={readMine} /> Read my lines out too (just listening)</label>
        <label class="tick"><input type="checkbox" bind:checked={listen} disabled={readMine} /> Move on by itself when I've said my line (uses the microphone)</label>
      </section>

      {#if data.audio.total === 0}
        <p class="warn small">Nobody has a voice yet. Give the characters voices on the Cast tab first.</p>
      {:else if data.audio.ready < data.audio.total}
        <p class="muted small">
          {data.audio.ready} of {data.audio.total} lines have their voice ready. The rest are being made; you can start now.
          <button class="btn small ghost" onclick={load}>Check again</button>
        </p>
      {/if}

      <button class="btn primary big" onclick={start} disabled={data.audio.total === 0}>
        {data.mine.length && !readMine ? 'Start' : 'Listen'}
      </button>
    </div>
  {:else if phase === 'done'}
    <div class="page done">
      <h1>That's the end.</h1>
      <p class="muted">Nicely done.</p>
      <button class="btn primary big" onclick={begin}>Go again</button>
      <button class="btn big" onclick={leave}>Change something</button>
      <a class="btn ghost" href="/script/{id}">Back to the script</a>
    </div>
  {:else}
    <div class="stage">
      <div class="topbar">
        <button class="btn small" onclick={leave}>Stop</button>
        <span class="spacer"></span>
        <div class="seg" role="radiogroup" aria-label="How to see the script">
          {#each views as v}
            <button role="radio" aria-checked={view === v.id} class:on={view === v.id} onclick={() => (view = v.id)}>{v.label}</button>
          {/each}
        </div>
      </div>

      {#if view === 'offbook'}
        <div class="blind" aria-live="polite">
          {#if !current || current.kind !== 'dialogue'}
            <p class="muted">…</p>
          {:else if myTurn}
            <p class="turn">Your line</p>
            <p class="muted">{who(current)}</p>
            {#if hint}<p class="hinttext">{hintText(current)}</p>{/if}
          {:else if status === 'paused'}
            <p class="turn">Paused</p>
          {:else}
            <p class="speaker" style:--h={cast.get(current.speakers[0])?.hue}>{who(current)}</p>
            <p class="muted">{status === 'loading' ? 'getting ready…' : status === 'yourturn' ? 'tap to carry on' : 'is speaking'}</p>
          {/if}
        </div>
      {:else}
        <ol class="lines">
          {#each seq as l, i (l.line_id)}
            <li id="l{l.line_id}" class="{l.kind}" class:now={i === idx} class:mine={l.mine} class:past={i < idx}>
              {#if l.kind === 'dialogue'}
                <span class="names cname" style:--h={cast.get(l.speakers[0])?.hue}>{who(l)}{#if l.mine} <span class="you">you</span>{/if}</span>
                {#if view === 'cues' && l.mine && !readMine && i >= idx}
                  <span class="text hidden">{i === idx && hint ? hintText(l) : i === idx ? 'Your line…' : '• • •'}</span>
                {:else}
                  <span class="text">{l.text}</span>
                {/if}
              {:else}
                <span class="text">{l.text}</span>
              {/if}
            </li>
          {/each}
        </ol>
      {/if}

      {#if myTurn && hear !== 'off'}
        <p class="ear" class:live={hear === 'speaking'} aria-live="polite">
          {hear === 'waiting' ? 'Listening…' : hear === 'speaking' ? 'Hearing you' : hear === 'pausing' ? 'Moving on…' : hear === 'thinking' ? 'Take your time' : hear === 'nudge' ? "Stuck? Try Hint, or tap when you've said it." : ''}
        </p>
      {/if}
      <div class="controls">
        <button class="btn" onclick={back} aria-label="Back one line">‹ Back</button>
        {#if status === 'paused'}
          <span class="spacer"></span>
        {:else}
          <button class="btn" onclick={pause}>Pause</button>
        {/if}
        {#if myTurn && view !== 'script'}
          <button class="btn" class:pulse={hear === 'nudge'} onclick={() => (hint += HINT_STEP)}>Hint</button>
        {/if}
        <button class="btn primary main" class:pulse={myTurn && hear === 'off'} onclick={next} disabled={status === 'loading'}>
          {#if progress}<span class="fill" style:width="{progress * 100}%"></span>{/if}
          {status === 'paused' ? 'Carry on' : status === 'yourturn' ? (myTurn ? "I've said it" : 'Next') : status === 'speaking' ? 'Skip' : '…'}
        </button>
      </div>
    </div>
  {/if}
{/if}

<style>
  section { margin: 1.25rem 0; }
  h2 { font-size: 1.05rem; margin-bottom: 0.5rem; }
  .warn { color: var(--danger); }
  .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.4rem; }
  .chip { border: 2px solid hsl(var(--h) 55% 50%); color: var(--text); background: transparent; border-radius: 999px; padding: 0.3rem 0.8rem; min-height: 44px; font-weight: 600; font-size: 0.9rem; }
  .chip.on { background: hsl(var(--h) 55% 40%); border-color: hsl(var(--h) 55% 40%); color: #fff; }
  .viewpick { display: grid; gap: 0.5rem; }
  .viewpick button { display: grid; gap: 0.1rem; text-align: left; padding: 0.7rem 0.9rem; border-radius: 12px; border: 2px solid var(--line); background: var(--surface); }
  .viewpick button span { color: var(--muted); }
  .viewpick button.on { border-color: var(--accent); }
  .tick { display: flex; gap: 0.5rem; align-items: center; color: var(--text); margin-top: 0.75rem; }
  .big { width: 100%; min-height: 56px; font-size: 1.1rem; margin-top: 0.5rem; }
  .done { text-align: center; padding-top: 3rem; }

  /* full-screen while rehearsing: nothing else on the page should scroll or distract */
  .stage { position: fixed; inset: 0; z-index: 60; display: flex; flex-direction: column; background: var(--bg); padding-top: env(safe-area-inset-top); }
  .topbar { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .seg button { background: var(--surface); border: 0; padding: 0.45rem 0.7rem; font-size: 0.85rem; }
  .seg button.on { background: var(--accent); color: var(--accent-ink); font-weight: 600; }

  .lines { flex: 1; overflow-y: auto; list-style: none; margin: 0; padding: 0.5rem 0.75rem 40vh; }
  .lines li { padding: 0.45rem 0.6rem; border-radius: 8px; border-left: 4px solid transparent; }
  .lines li.past { opacity: 0.55; }
  .lines li.now { background: var(--surface-2); opacity: 1; }
  .lines li.mine { border-left-color: var(--accent); }
  .names { display: block; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.03em; }
  .cname { color: hsl(var(--h) 60% var(--cast-l)); }
  .you { margin-left: 0.4rem; font-size: 0.7rem; padding: 0 0.35rem; border-radius: 4px; background: var(--accent); color: var(--accent-ink); }
  .text { display: block; white-space: pre-wrap; overflow-wrap: anywhere; }
  .text.hidden { color: var(--muted); font-style: italic; }
  li.direction .text { font-style: italic; color: var(--muted); }
  li.heading .text { font-weight: 700; text-align: center; letter-spacing: 0.05em; padding: 0.4rem 0; }

  .blind { flex: 1; display: grid; place-content: center; text-align: center; gap: 0.25rem; padding: 1rem; }
  .blind p { margin: 0; }
  .speaker { font-size: 2rem; font-weight: 800; color: hsl(var(--h) 60% var(--cast-l)); }
  .turn { font-size: 2.2rem; font-weight: 800; color: var(--accent); }
  .hinttext { font-size: 1.4rem; margin-top: 1rem !important; }

  .controls { display: flex; gap: 0.5rem; align-items: center; padding: 0.6rem 0.75rem calc(0.6rem + env(safe-area-inset-bottom)); border-top: 1px solid var(--line); background: var(--surface); }
  .controls .main { flex: 1; min-height: 60px; font-size: 1.1rem; position: relative; overflow: hidden; }
  /* fills left to right during the pause before moving on, so it never comes as a surprise */
  .fill { position: absolute; inset: 0 auto 0 0; background: rgb(255 255 255 / 0.35); transition: width 80ms linear; }
  .ear { margin: 0; padding: 0.3rem 0.75rem; text-align: center; font-size: 0.85rem; color: var(--muted); }
  .ear.live { color: var(--accent); font-weight: 600; }
  .pulse { animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--accent) 25%, transparent); } }
  @media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
</style>
