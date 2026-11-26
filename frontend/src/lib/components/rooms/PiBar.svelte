<script lang="ts">
  // Kompakter Balken 0 bis 100 %; die Zahl daneben trägt die Information.
  let { percent }: { percent: number | null } = $props();
</script>

<div class="h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
  {#if percent !== null}
    <div class="h-full rounded-full bg-[var(--h-pi)]" style="width:{Math.max(0, Math.min(100, percent))}%"></div>
  {/if}
</div>
