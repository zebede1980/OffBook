<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '#lib/api';
  import Avatar from '#lib/components/Avatar.svelte';
  import { session } from '#lib/session.svelte';
  import type { Me } from '#lib/types';

  let { children } = $props();
  let ready = $state(false);

  onMount(async () => {
    if (!session.profile) {
      try {
        session.profile = (await api.get<Me>('/api/me')).profile;
      } catch {
        return; // api.ts has already sent us to /login
      }
    }
    if (!session.profile) {
      await goto('/who', { replaceState: true });
      return;
    }
    ready = true;
  });
</script>

{#if ready && session.profile}
  <header class="top">
    <a class="brand" href="/"><img src="/icon.svg" alt="" width="28" height="28" /> OffBook</a>
    <span class="spacer"></span>
    <a class="me" href="/who" aria-label="Switch person (currently {session.profile.name})">
      <Avatar name={session.profile.name} colour={session.profile.colour} size={32} />
      <span>{session.profile.name}</span>
    </a>
  </header>
  <main>
    {@render children()}
  </main>
{/if}

<style>
  .top { display: flex; align-items: center; gap: 0.5rem; padding: calc(0.6rem + env(safe-area-inset-top)) 1rem 0.6rem; border-bottom: 1px solid var(--line); background: var(--surface); position: sticky; top: 0; z-index: 40; }
  .brand { display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 700; font-size: 1.15rem; color: var(--text); }
  .brand img { border-radius: 7px; }
  .me { display: inline-flex; align-items: center; gap: 0.45rem; color: var(--text); font-weight: 600; min-height: 44px; }
  .brand:hover, .me:hover { text-decoration: none; }
  main { padding-bottom: calc(1.5rem + env(safe-area-inset-bottom)); }
</style>
