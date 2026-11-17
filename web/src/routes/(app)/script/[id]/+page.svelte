<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '#lib/api';
  import { shrinkImage } from '#lib/images';
  import { castHue, segments } from '#lib/text';
  import { errorToast, toast } from '#lib/toast.svelte';
  import type { Character, Kind, Line, ScriptDetail, Voice } from '#lib/types';

  const id = Number(page.params.id);
  const kinds: { id: Kind; label: string }[] = [
    { id: 'dialogue', label: 'Speech' },
    { id: 'direction', label: 'Stage direction' },
    { id: 'heading', label: 'Heading' }
  ];

  let detail = $state<ScriptDetail | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let tab = $state<'lines' | 'cast'>('lines');
  let uploading = $state('');
  let onlyCheck = $state(false);

  let editing = $state<number | null>(null);
  let draft = $state({ text: '', kind: 'dialogue' as Kind, speakers: [] as number[] });
  let area = $state<HTMLTextAreaElement | null>(null);
  let newName = $state('');
  let saving = $state(false);

  let photo = $state<number | null>(null);
  let armed = $state(false);
  let renaming = $state(false);
  let title = $state('');
  let mergeFrom = $state(0);
  let mergeTo = $state(0);
  let deleting = $state(false);
  let password = $state('');
  let voices = $state<Voice[]>([]);
  let mine = $state<number[]>([]);
  let sample: HTMLAudioElement | null = null;

  const cast = $derived(
    new Map((detail?.characters ?? []).map((c, i) => [c.character_id, { ...c, hue: castHue(i) }]))
  );
  const needsLook = (l: Line) => l.unsure.length > 0 || (l.kind === 'dialogue' && l.speakers.length === 0);
  const toCheck = $derived((detail?.lines ?? []).filter(needsLook).length);
  const shown = $derived(
    (detail?.lines ?? []).filter((l) => !onlyCheck || needsLook(l) || l.line_id === editing)
  );
  const reading = $derived((detail?.pages ?? []).filter((p) => p.status === 'queued').length);
  const photoPage = $derived(detail?.pages.find((p) => p.page_id === photo) ?? null);

  function apply(d: ScriptDetail) {
    detail = d;
    clearTimeout(timer);
    // pages are read in the background; keep looking until none are waiting
    if (d.pages.some((p) => p.status === 'queued')) timer = setTimeout(load, 2500);
  }

  async function load() {
    try {
      apply(await api.get<ScriptDetail>(`/api/scripts/${id}`));
    } catch (e) {
      errorToast(e);
    }
  }

  onMount(() => {
    load();
    // the cast tab's extras; the script is usable without them, so a failure here is not shown as an error
    api.get<{ voices: Voice[] }>('/api/voices').then((r) => (voices = r.voices)).catch(() => {});
    api.get<{ character_ids: number[] }>(`/api/scripts/${id}/casting`).then((r) => (mine = r.character_ids)).catch(() => {});
  });
  onDestroy(() => {
    clearTimeout(timer);
    sample?.pause();
  });

  const voiceGroups = $derived(
    [...new Set(voices.map((v) => v.group))].map((g) => ({ group: g, voices: voices.filter((v) => v.group === g) }))
  );

  async function setVoice(c: Character, voice: string) {
    try {
      await api.patch(`/api/characters/${c.character_id}/voice`, { voice: voice || null, speed: c.speed });
      await load();
    } catch (err) {
      errorToast(err);
    }
  }

  function playSample(voice: string | null) {
    if (!voice) return;
    sample?.pause();
    sample = new Audio(`/api/voices/${voice}/preview`);
    sample.play().catch(() => toast("Couldn't play that voice just now."));
  }

  async function toggleMine(c: Character) {
    const next = mine.includes(c.character_id) ? mine.filter((x) => x !== c.character_id) : [...mine, c.character_id];
    try {
      mine = (await api.put<{ character_ids: number[] }>(`/api/scripts/${id}/casting`, { character_ids: next })).character_ids;
    } catch (err) {
      errorToast(err);
    }
  }

  async function addPages(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    // camera files are numbered in the order they were taken, so name order is reading order
    const files = [...(input.files ?? [])].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    input.value = '';
    if (!files.length) return;
    try {
      const form = new FormData();
      for (const [i, f] of files.entries()) {
        uploading = `Getting photo ${i + 1} of ${files.length} ready…`;
        form.append('files', await shrinkImage(f), `page-${i + 1}.jpg`);
      }
      uploading = 'Sending…';
      apply(await api.upload<ScriptDetail>(`/api/scripts/${id}/pages`, form));
    } catch (err) {
      errorToast(err);
    } finally {
      uploading = '';
    }
  }

  function open(l: Line) {
    editing = l.line_id;
    draft = { text: l.text, kind: l.kind, speakers: [...l.speakers] };
    newName = '';
  }

  function toggle(cid: number) {
    draft.speakers = draft.speakers.includes(cid) ? draft.speakers.filter((x) => x !== cid) : [...draft.speakers, cid];
  }

  const putDraft = () =>
    api.patch<Line>(`/api/lines/${editing}`, {
      text: draft.text,
      kind: draft.kind,
      speakers: draft.kind === 'dialogue' ? draft.speakers : []
    });

  /** Run one change to the script, then close the editor and re-read everything (line order may have moved). */
  async function change(fn: () => Promise<unknown>, reopen?: () => Line | undefined) {
    saving = true;
    try {
      await fn();
      editing = null;
      await load();
      const next = reopen?.();
      if (next) open(next);
    } catch (e) {
      errorToast(e);
    } finally {
      saving = false;
    }
  }

  const save = () => change(putDraft);
  const remove = () => change(() => api.post(`/api/lines/${editing}/delete`));
  const joinUp = () => change(async () => { await putDraft(); await api.post(`/api/lines/${editing}/merge-up`); });

  function splitAtCursor() {
    const lead = draft.text.length - draft.text.trimStart().length;
    const at = (area?.selectionStart ?? 0) - lead;
    if (at <= 0 || at >= draft.text.trim().length) {
      toast('Tap inside the line where it should break, then split.');
      return;
    }
    change(async () => { await putDraft(); await api.post(`/api/lines/${editing}/split`, { at }); });
  }

  function addLine(after: number | null) {
    let made: Line | undefined;
    change(
      async () => {
        if (after !== null && after === editing) await putDraft();
        made = await api.post<Line>(`/api/scripts/${id}/lines`, { after_line_id: after, kind: 'dialogue', text: 'New line', speakers: [] });
      },
      () => detail?.lines.find((l) => l.line_id === made?.line_id)
    );
  }

  async function addCharacter(e: SubmitEvent) {
    e.preventDefault();
    try {
      const c = await api.post<{ character_id: number }>(`/api/scripts/${id}/characters`, { name: newName });
      await load();
      if (!draft.speakers.includes(c.character_id)) draft.speakers = [...draft.speakers, c.character_id];
      newName = '';
    } catch (err) {
      errorToast(err);
    }
  }

  async function renameCharacter(c: Character, name: string) {
    if (name.trim() === c.name) return;
    try {
      await api.patch(`/api/characters/${c.character_id}`, { name });
    } catch (err) {
      errorToast(err);
    }
    await load();
  }

  async function deleteCharacter(c: Character) {
    try {
      await api.post(`/api/characters/${c.character_id}/delete`);
      await load();
    } catch (err) {
      errorToast(err);
    }
  }

  async function merge(e: SubmitEvent) {
    e.preventDefault();
    try {
      apply(await api.post<ScriptDetail>(`/api/characters/${mergeFrom}/merge`, { into: mergeTo }));
      mergeFrom = mergeTo = 0;
      toast('Merged.');
    } catch (err) {
      errorToast(err);
    }
  }

  function showPhoto(pageId: number) {
    photo = pageId;
    armed = false;
  }

  async function retryPage() {
    try {
      apply(await api.post<ScriptDetail>(`/api/pages/${photo}/retry`));
      photo = null;
    } catch (err) {
      errorToast(err);
    }
  }

  async function deletePage() {
    if (!armed) {
      armed = true;
      return;
    }
    try {
      apply(await api.post<ScriptDetail>(`/api/pages/${photo}/delete`));
      photo = null;
    } catch (err) {
      errorToast(err);
    }
  }

  async function rename(e: SubmitEvent) {
    e.preventDefault();
    try {
      await api.patch(`/api/scripts/${id}`, { title });
      renaming = false;
      await load();
    } catch (err) {
      errorToast(err);
    }
  }

  async function deleteScript(e: SubmitEvent) {
    e.preventDefault();
    try {
      await api.post(`/api/scripts/${id}/delete`, { password });
      await goto('/', { replaceState: true });
    } catch (err) {
      errorToast(err);
    }
  }
</script>

{#if detail}
  <div class="page">
    <a class="small" href="/">‹ All scripts</a>

    {#if renaming}
      <form class="row rename" onsubmit={rename}>
        <input type="text" maxlength="80" aria-label="Script title" bind:value={title} required />
        <button class="btn primary" type="submit">Save</button>
        <button class="btn ghost" type="button" onclick={() => (renaming = false)}>Cancel</button>
      </form>
    {:else}
      <div class="row head">
        <h1>{detail.script.title}</h1>
        <button class="btn small ghost" onclick={() => { title = detail!.script.title; renaming = true; }}>Rename</button>
      </div>
    {/if}

    {#if detail.lines.length}<a class="btn primary go" href="/script/{id}/rehearse">Rehearse</a>{/if}

    <div class="strip">
      {#each detail.pages as p (p.page_id)}
        <button class="thumb" onclick={() => showPhoto(p.page_id)} aria-label="Page {p.seq}{p.status === 'failed' ? ', has a problem' : ''}">
          <img src="/api/pages/{p.page_id}/image" alt="" loading="lazy" />
          {#if p.status === 'queued'}<span class="tag">Reading…</span>{/if}
          {#if p.status === 'failed'}<span class="tag bad">Problem</span>{/if}
        </button>
      {/each}
      <label class="thumb addp" class:off={!!uploading}>
        <input class="sr-only" type="file" accept="image/*" multiple onchange={addPages} disabled={!!uploading} />
        <span>+ Add pages</span>
      </label>
      <label class="thumb addp" class:off={!!uploading}>
        <input class="sr-only" type="file" accept="image/*" capture="environment" onchange={addPages} disabled={!!uploading} />
        <span>Take a photo</span>
      </label>
    </div>
    {#if uploading}<p class="muted small">{uploading}</p>{/if}
    {#if reading}<p class="muted small">Reading {reading} page{reading === 1 ? '' : 's'}. Each takes a few seconds.</p>{/if}

    <div class="tabs" role="tablist">
      <button role="tab" aria-selected={tab === 'lines'} class:on={tab === 'lines'} onclick={() => (tab = 'lines')}>Lines</button>
      <button role="tab" aria-selected={tab === 'cast'} class:on={tab === 'cast'} onclick={() => (tab = 'cast')}>
        Cast ({detail.characters.length})
      </button>
    </div>

    {#if tab === 'lines'}
      {#if toCheck}
        <label class="check">
          <input type="checkbox" bind:checked={onlyCheck} />
          Only show the {toCheck} that need{toCheck === 1 ? 's' : ''} checking
        </label>
      {/if}

      {#if !detail.lines.length}
        <div class="card empty">
          <p><strong>No lines yet.</strong></p>
          <p class="small">Add photos of the pages, in order, and the lines will appear here.</p>
          <button class="btn small" onclick={() => addLine(null)}>Or type a line in</button>
        </div>
      {/if}

      <ol class="lines">
        {#each shown as l (l.line_id)}
          <li>
            {#if editing === l.line_id}
              <div class="card editor">
                <div class="seg" role="radiogroup" aria-label="What kind of line is this?">
                  {#each kinds as k}
                    <button type="button" role="radio" aria-checked={draft.kind === k.id} class:on={draft.kind === k.id} onclick={() => (draft.kind = k.id)}>{k.label}</button>
                  {/each}
                </div>

                {#if draft.kind === 'dialogue'}
                  <p class="small muted lbl">Who says it?</p>
                  <div class="who">
                    {#each detail.characters as c (c.character_id)}
                      <button type="button" class="chip" aria-pressed={draft.speakers.includes(c.character_id)} class:on={draft.speakers.includes(c.character_id)} style:--h={cast.get(c.character_id)?.hue} onclick={() => toggle(c.character_id)}>{c.name}</button>
                    {/each}
                  </div>
                  <form class="row newc" onsubmit={addCharacter}>
                    <input type="text" maxlength="40" placeholder="Someone new" aria-label="New character name" bind:value={newName} />
                    <button class="btn small" type="submit" disabled={!newName.trim()}>Add</button>
                  </form>
                {/if}

                <textarea rows="4" aria-label="The line" bind:this={area} bind:value={draft.text}></textarea>
                {#if l.unsure.length}<p class="small warn">The reader wasn't sure about: {l.unsure.join(', ')}</p>{/if}

                <div class="row">
                  <button class="btn primary" onclick={save} disabled={saving || !draft.text.trim()}>Save</button>
                  <button class="btn ghost" onclick={() => (editing = null)}>Cancel</button>
                  <span class="spacer"></span>
                  {#if l.page_id}<button class="btn small" onclick={() => showPhoto(l.page_id!)}>See the page</button>{/if}
                </div>
                <div class="row more">
                  <button class="btn small" onclick={splitAtCursor} disabled={saving}>Split at cursor</button>
                  <button class="btn small" onclick={joinUp} disabled={saving}>Join to line above</button>
                  <button class="btn small" onclick={() => addLine(l.line_id)} disabled={saving}>Add line below</button>
                  <button class="btn small danger" onclick={remove} disabled={saving}>Delete</button>
                </div>
              </div>
            {:else}
              <button class="line {l.kind}" class:flag={needsLook(l)} onclick={() => open(l)}>
                {#if l.kind === 'dialogue'}
                  <span class="names">
                    {#each l.speakers as cid, i}
                      {#if i}<span class="muted"> &amp; </span>{/if}<span class="cname" style:--h={cast.get(cid)?.hue}>{cast.get(cid)?.name}</span>
                    {:else}
                      <span class="warn">Who says this?</span>
                    {/each}
                    {#if l.highlighted}<span class="hl" title="Highlighted on the page">highlighted</span>{/if}
                  </span>
                {/if}
                <span class="text">{#each segments(l.text, l.unsure) as s}{#if s.mark}<mark>{s.t}</mark>{:else}{s.t}{/if}{/each}</span>
              </button>
            {/if}
          </li>
        {/each}
      </ol>
    {:else}
      {#each detail.characters as c, i (c.character_id)}
        <div class="card castrow" class:me={mine.includes(c.character_id)}>
          <div class="row">
            <span class="dot" style:--h={castHue(i)}></span>
            <input type="text" maxlength="40" aria-label="Character name" value={c.name} onchange={(e) => renameCharacter(c, e.currentTarget.value)} />
            <span class="muted small count">{c.lines} line{c.lines === 1 ? '' : 's'}</span>
            {#if c.lines === 0}<button class="btn small danger" onclick={() => deleteCharacter(c)}>Remove</button>{/if}
          </div>
          <div class="row">
            <button class="btn small mebtn" aria-pressed={mine.includes(c.character_id)} class:on={mine.includes(c.character_id)} onclick={() => toggleMine(c)}>
              {mine.includes(c.character_id) ? "That's me" : 'This is me'}
            </button>
            <select aria-label="Voice for {c.name}" value={c.voice ?? ''} onchange={(e) => setVoice(c, e.currentTarget.value)}>
              <option value="">No voice</option>
              {#each voiceGroups as g}
                <optgroup label={g.group}>
                  {#each g.voices as v}<option value={v.id}>{v.label}</option>{/each}
                </optgroup>
              {/each}
            </select>
            <button class="btn small" aria-label="Hear {c.name}'s voice" disabled={!c.voice} onclick={() => playSample(c.voice)}>▶</button>
          </div>
        </div>
      {:else}
        <div class="card empty"><p>Characters appear here once some pages have been read.</p></div>
      {/each}

      {#if detail.characters.length > 1}
        <form class="card mergebox" onsubmit={merge}>
          <h2>Same person twice?</h2>
          <p class="small muted">If one character has been listed under two names, merge them.</p>
          <div class="row mergerow">
            <select aria-label="Character to fold away" bind:value={mergeFrom}>
              <option value={0}>Choose…</option>
              {#each detail.characters as c (c.character_id)}<option value={c.character_id}>{c.name}</option>{/each}
            </select>
            <span class="small">is really</span>
            <select aria-label="Character to keep" bind:value={mergeTo}>
              <option value={0}>Choose…</option>
              {#each detail.characters as c (c.character_id)}<option value={c.character_id}>{c.name}</option>{/each}
            </select>
          </div>
          <button class="btn" type="submit" disabled={!mergeFrom || !mergeTo || mergeFrom === mergeTo}>Merge</button>
        </form>
      {/if}
    {/if}

    <div class="danger-zone">
      {#if deleting}
        <form class="card delbox" onsubmit={deleteScript}>
          <p><strong>Delete "{detail.script.title}" for everyone?</strong> This can't be undone.</p>
          <label for="dp">Family password</label>
          <input id="dp" type="password" autocomplete="current-password" bind:value={password} required />
          <div class="row">
            <button class="btn danger" type="submit">Delete it</button>
            <button class="btn ghost" type="button" onclick={() => (deleting = false)}>Keep it</button>
          </div>
        </form>
      {:else}
        <button class="btn ghost small muted" onclick={() => { password = ''; deleting = true; }}>Delete this script</button>
      {/if}
    </div>
  </div>

  {#if photoPage}
    <div class="overlay" role="dialog" aria-modal="true" aria-label="Photo of page {photoPage.seq}">
      <div class="bar">
        <strong>Page {photoPage.seq}</strong>
        <span class="spacer"></span>
        {#if photoPage.status === 'failed'}<button class="btn small" onclick={retryPage}>Try reading again</button>{/if}
        <button class="btn small danger" onclick={deletePage}>{armed ? 'Yes, delete page and its lines' : 'Delete page'}</button>
        <button class="btn small" onclick={() => (photo = null)}>Close</button>
      </div>
      {#if photoPage.error}<p class="err">{photoPage.error}</p>{/if}
      <div class="scroll"><img src="/api/pages/{photoPage.page_id}/image" alt="Photo of page {photoPage.seq}" /></div>
    </div>
  {/if}
{/if}

<style>
  .head { margin-top: 0.4rem; align-items: baseline; }
  .head h1 { margin: 0; flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .rename { margin-top: 0.5rem; }
  .warn { color: var(--danger); }

  .strip { display: flex; gap: 0.5rem; overflow-x: auto; padding: 0.75rem 0 0.5rem; }
  .thumb { position: relative; flex: none; width: 72px; height: 96px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface-2); padding: 0; overflow: hidden; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .tag { position: absolute; left: 0; right: 0; bottom: 0; font-size: 0.65rem; padding: 2px; background: rgb(0 0 0 / 0.7); color: #fff; }
  .tag.bad { background: var(--danger); }
  .addp { display: grid; place-items: center; text-align: center; font-size: 0.78rem; color: var(--muted); border-style: dashed; cursor: pointer; margin: 0; padding: 0.25rem; }
  .addp.off { opacity: 0.5; }
  .addp:focus-within { outline: 2px solid var(--accent); }

  .tabs { display: flex; gap: 0.25rem; border-bottom: 1px solid var(--line); margin: 0.5rem 0 0.75rem; }
  .tabs button { background: none; border: 0; border-bottom: 3px solid transparent; padding: 0.6rem 0.9rem; color: var(--muted); font-weight: 600; }
  .tabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
  .check { display: flex; gap: 0.5rem; align-items: center; margin: 0 0 0.75rem; color: var(--text); }

  .lines { list-style: none; margin: 0; padding: 0; }
  .line { display: block; width: 100%; text-align: left; background: none; border: 0; border-left: 3px solid transparent; padding: 0.45rem 0.6rem; border-radius: 6px; }
  .line:hover, .line:focus-visible { background: var(--surface-2); }
  .line.flag { border-left-color: var(--danger); }
  .names { display: block; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.03em; }
  .cname { color: hsl(var(--h) 60% var(--cast-l)); }
  .hl { margin-left: 0.5rem; font-weight: 500; font-size: 0.7rem; padding: 0 0.35rem; border-radius: 4px; background: #ffe45c; color: #3a3000; }
  .text { display: block; white-space: pre-wrap; overflow-wrap: anywhere; }
  .line.direction .text { font-style: italic; color: var(--muted); }
  .line.heading .text { font-weight: 700; text-align: center; letter-spacing: 0.05em; padding: 0.4rem 0; }
  mark { background: #ffe45c; color: #3a3000; border-radius: 3px; padding: 0 2px; }

  .editor { padding: 0.75rem; margin: 0.4rem 0; }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .seg button { flex: 1; background: var(--surface); border: 0; padding: 0.5rem 0.25rem; font-size: 0.85rem; }
  .seg button.on { background: var(--accent); color: var(--accent-ink); font-weight: 600; }
  .lbl { margin: 0.75rem 0 0.35rem; }
  .who { display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .chip { border: 2px solid hsl(var(--h) 55% 50%); color: var(--text); background: transparent; border-radius: 999px; padding: 0.3rem 0.8rem; min-height: 40px; font-weight: 600; font-size: 0.85rem; }
  .chip.on { background: hsl(var(--h) 55% 40%); border-color: hsl(var(--h) 55% 40%); color: #fff; }
  .editor .newc { margin: 0.5rem 0 0; flex-wrap: nowrap; }
  .newc input { min-height: 40px; flex: 1; min-width: 0; }
  textarea { width: 100%; margin-top: 0.75rem; min-height: 96px; resize: vertical; }
  .editor .row { margin-top: 0.6rem; flex-wrap: wrap; }
  .more { padding-top: 0.6rem; border-top: 1px solid var(--line); }

  .castrow { display: grid; gap: 0.5rem; padding: 0.6rem 0.75rem; margin-bottom: 0.5rem; }
  .castrow.me { border-color: var(--accent); }
  .castrow input { flex: 1; min-width: 0; min-height: 40px; }
  .castrow select { flex: 1; min-width: 0; min-height: 40px; }
  .mebtn { white-space: nowrap; }
  .mebtn.on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }
  .go { width: 100%; margin: 0.25rem 0 0.25rem; }
  .dot { width: 14px; height: 14px; border-radius: 50%; flex: none; background: hsl(var(--h) 55% 50%); }
  .count { white-space: nowrap; }
  .mergebox { padding: 1rem; margin-top: 1rem; }
  .mergerow { flex-wrap: wrap; margin-bottom: 0.75rem; }
  .mergerow select { flex: 1; min-width: 120px; }

  .danger-zone { margin-top: 2.5rem; text-align: center; }
  .delbox { padding: 1rem; text-align: left; }
  .delbox .row { margin-top: 0.9rem; }

  .overlay { position: fixed; inset: 0; z-index: 80; background: var(--bg); display: flex; flex-direction: column; }
  .bar { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; padding: calc(0.5rem + env(safe-area-inset-top)) 0.75rem 0.5rem; border-bottom: 1px solid var(--line); background: var(--surface); }
  .err { margin: 0; padding: 0.5rem 0.75rem; color: var(--danger); }
  .scroll { flex: 1; overflow: auto; }
  .scroll img { display: block; width: 100%; height: auto; }
</style>
