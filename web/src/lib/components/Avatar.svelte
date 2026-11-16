<script lang="ts">
  let { name, colour, size = 72 }: { name: string; colour: string; size?: number } = $props();
</script>

<span class="avatar" style:background={colour} style:width="{size}px" style:height="{size}px" style:font-size="{size * 0.45}px" aria-hidden="true">
  {name.slice(0, 1).toUpperCase()}
</span>

<style>
  .avatar { display: inline-grid; place-items: center; border-radius: 50%; color: #fff; font-weight: 700; line-height: 1; flex: none; }
</style>
