<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '#lib/api';
  import { session } from '#lib/session.svelte';
  import { errorToast } from '#lib/toast.svelte';
  import type { ScriptDetail, ScriptSummary } from '#lib/types';

  let scripts = $state<ScriptSummary[] | null>(null);
  let creating = $state(false);
  let title = $state('');
  let busy = $state(false);

  onMount(async () => {
    try {
      scripts = (await api.get<{ scripts: ScriptSummary[] }>('/api/scripts')).scripts;
    } catch (e) {
      errorToast(e);
    }
  });

  async function create(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    try {
      const d = await api.post<ScriptDetail>('/api/scripts', { title });
      await goto(`/script/${d.script.script_id}`);
    } catch (err) {
      errorToast(err);
    } finally {
      busy = false;
    }
  }

  function summary(s: ScriptSummary): string {
    if (s.reading) return `Reading ${s.reading} page${s.reading === 1 ? '' : 's'}…`;
    if (!s.speeches) return 'No lines yet';
    return `${s.speeches} speeches · ${s.characters} characters`;
  }
</script>

<div class="page">
  <h1>Hi {session.profile?.name}</h1>

  {#if scripts}
    {#each scripts as s (s.script_id)}
      <a class="card script" href="/script/{s.script_id}">
        <strong>{s.title}</strong>
        <span class="muted small">{summary(s)}</span>
        {#if s.failed}<span class="warn small">{s.failed} page{s.failed === 1 ? '' : 's'} couldn't be read</span>{/if}
      </a>
    {:else}
      <div class="card empty">
        <p><strong>No scripts yet.</strong></p>
        <p class="small">Add one, then photograph its pages.</p>
      </div>
    {/each}

    {#if creating}
      <form class="card new" onsubmit={create}>
        <label for="t">What's the play called?</label>
        <input id="t" type="text" maxlength="80" autocomplete="off" bind:value={title} required />
        <div class="row">
          <button class="btn primary" type="submit" disabled={busy || !title.trim()}>Add it</button>
          <button class="btn ghost" type="button" onclick={() => (creating = false)}>Cancel</button>
        </div>
      </form>
    {:else}
      <button class="btn primary add" onclick={() => (creating = true)}>+ New script</button>
    {/if}
  {/if}
</div>

<style>
  .script { display: grid; gap: 0.15rem; padding: 0.9rem 1rem; margin-bottom: 0.75rem; color: var(--text); }
  .script:hover { text-decoration: none; border-color: var(--accent); }
  .warn { color: var(--danger); }
  .new { padding: 0.25rem 1rem 1rem; }
  .new .row { margin-top: 0.9rem; }
  .add { width: 100%; margin-top: 0.25rem; }
</style>
