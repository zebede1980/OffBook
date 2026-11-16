<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '#lib/api';
  import Avatar from '#lib/components/Avatar.svelte';
  import { session } from '#lib/session.svelte';
  import { errorToast } from '#lib/toast.svelte';
  import type { Profile } from '#lib/types';

  let profiles = $state<Profile[]>([]);
  let colours = $state<string[]>([]);
  let loaded = $state(false);
  let adding = $state(false);
  let name = $state('');
  let colour = $state('');
  let busy = $state(false);

  onMount(async () => {
    try {
      const r = await api.get<{ profiles: Profile[]; colours: string[] }>('/api/profiles');
      profiles = r.profiles;
      colours = r.colours;
      const used = new Set(r.profiles.map((p) => p.colour));
      colour = r.colours.find((c) => !used.has(c)) ?? r.colours[0];
      adding = r.profiles.length === 0;
      loaded = true;
    } catch (e) {
      errorToast(e);
    }
  });

  async function pick(p: Profile) {
    try {
      session.profile = await api.post<Profile>(`/api/profiles/${p.profile_id}/select`);
      await goto('/', { replaceState: true });
    } catch (e) {
      errorToast(e);
    }
  }

  async function add(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    try {
      await pick(await api.post<Profile>('/api/profiles', { name, colour }));
    } catch (err) {
      errorToast(err);
    } finally {
      busy = false;
    }
  }

  async function logout() {
    await api.post('/api/logout');
    session.profile = null;
    await goto('/login', { replaceState: true });
  }
</script>

{#if loaded}
  <main class="who">
    <h1>Who's rehearsing?</h1>

    <div class="people">
      {#each profiles as p (p.profile_id)}
        <button class="person" onclick={() => pick(p)}>
          <Avatar name={p.name} colour={p.colour} size={88} />
          <span>{p.name}</span>
        </button>
      {/each}
      {#if !adding}
        <button class="person" onclick={() => (adding = true)}>
          <span class="plus" aria-hidden="true">+</span>
          <span>Add someone</span>
        </button>
      {/if}
    </div>

    {#if adding}
      <form class="card add" onsubmit={add}>
        <h2>{profiles.length ? 'Add someone' : "First, what's your name?"}</h2>
        <label for="n">Name</label>
        <input id="n" type="text" maxlength="24" autocomplete="off" bind:value={name} required />
        <fieldset>
          <legend>Colour</legend>
          <div class="swatches">
            {#each colours as c}
              <label class="swatch" style:background={c} class:on={colour === c}>
                <input class="sr-only" type="radio" name="colour" value={c} bind:group={colour} />
                <span class="sr-only">{c}</span>
              </label>
            {/each}
          </div>
        </fieldset>
        <div class="row">
          <button class="btn primary" type="submit" disabled={busy || !name.trim()}>That's me</button>
          {#if profiles.length}
            <button class="btn ghost" type="button" onclick={() => (adding = false)}>Cancel</button>
          {/if}
        </div>
      </form>
    {/if}

    <button class="btn ghost small out" onclick={logout}>Log out of OffBook</button>
  </main>
{/if}

<style>
  .who { min-height: 100dvh; padding: 2rem 1rem; max-width: 560px; margin: 0 auto; text-align: center; }
  .people { display: flex; flex-wrap: wrap; justify-content: center; gap: 1.25rem; margin: 1.5rem 0; }
  .person { display: grid; justify-items: center; gap: 0.4rem; background: none; border: 0; padding: 0.25rem; width: 104px; font-weight: 600; }
  .plus { display: inline-grid; place-items: center; width: 88px; height: 88px; border-radius: 50%; border: 2px dashed var(--line); color: var(--muted); font-size: 2.2rem; line-height: 1; }
  .add { text-align: left; padding: 1.25rem; }
  fieldset { border: 0; padding: 0; margin: 0.75rem 0 1.25rem; }
  legend { font-size: 0.9rem; color: var(--muted); padding: 0; margin-bottom: 0.4rem; }
  .swatches { display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .swatch { width: 44px; height: 44px; border-radius: 50%; margin: 0; cursor: pointer; border: 3px solid transparent; }
  .swatch.on { border-color: var(--text); box-shadow: 0 0 0 2px var(--surface) inset; }
  .out { margin-top: 2.5rem; color: var(--muted); }
</style>
