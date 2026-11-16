<script lang="ts">
  import { session } from '#lib/session.svelte';
</script>

<div class="page">
  <h1>Hi {session.profile?.name}</h1>
  <div class="card empty">
    <p><strong>No scripts yet.</strong></p>
    <p class="small">Photographing script pages is the next thing being built.</p>
  </div>
</div>
