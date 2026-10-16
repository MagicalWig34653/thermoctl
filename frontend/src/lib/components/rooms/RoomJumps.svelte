<script lang="ts">
  import { useApp } from '$lib/context';
  import { buildVisibleAreas } from '$lib/navigation';
  import { appHref } from '$lib/paths';
  import { roomJumps } from '$lib/rooms/jumps';

  let { roomId }: { roomId: number } = $props();
  const { prefix, session } = useApp();
  // Nur Seiten, die für dieses Konto sichtbar sind (Darstellung; jeder Endpunkt prüft erneut).
  const jumps = $derived(roomJumps(buildVisibleAreas(session.bootstrap?.navigation ?? []), roomId));
</script>

{#if jumps.length}
  <nav class="mb-4 flex flex-wrap gap-2" aria-label="Zu diesem Raum" data-testid="room-jumps">
    {#each jumps as jump (jump.to)}
      <a
        class="inline-flex min-h-10 items-center rounded-lg border bg-card px-3 text-sm font-medium hover:bg-muted"
        href={appHref(prefix, jump.to)}>{jump.label}</a
      >
    {/each}
  </nav>
{/if}
