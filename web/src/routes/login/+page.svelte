<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api, safeNext } from '#lib/api';

  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    error = '';
    try {
      await api.post('/api/login', { password });
      await goto(safeNext(page.url.searchParams.get('next')), { replaceState: true });
    } catch (err) {
      error = err instanceof Error ? err.message : 'Login failed';
    } finally {
      busy = false;
    }
  }
</script>

<main class="login">
  <form class="card" onsubmit={submit}>
    <img src="/icon.svg" alt="" width="56" height="56" />
    <h1>OffBook</h1>
    <p class="muted small">Someone to run your lines with.</p>
    <label for="p">Family password</label>
    <input id="p" type="password" autocomplete="current-password" bind:value={password} required />
    {#if error}<p class="err">{error}</p>{/if}
    <button class="btn primary" type="submit" disabled={busy}>{busy ? 'Opening…' : 'Come in'}</button>
  </form>
</main>

<style>
  .login { min-height: 100dvh; display: grid; place-items: center; padding: 1rem; }
  form { width: min(100%, 360px); padding: 1.5rem; }
  img { border-radius: 12px; margin-bottom: 0.75rem; }
  button { width: 100%; margin-top: 1.25rem; }
  .err { color: var(--danger); margin: 0.75rem 0 0; }
</style>
