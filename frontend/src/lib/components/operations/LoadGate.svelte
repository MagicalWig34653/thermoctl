<script lang="ts">
  import type { Snippet } from 'svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import type { OpsFailure } from '$lib/operations/logic';

  // Laden/Fehler/Inhalt. Die Daten kommen als Argument, damit der Inhalt nie ohne sie gerendert wird.
  let {
    loaded,
    loading,
    failure,
    onretry,
    children,
  }: { loaded: boolean; loading: boolean; failure: OpsFailure | null; onretry: () => void; children: Snippet } = $props();
</script>

{#if loaded}
  {#if failure}<div class="mb-4"><Notice tone="warn">{failure.message}</Notice></div>{/if}
  {@render children()}
{:else if loading}
  <p class="text-muted-foreground" role="status">Lade …</p>
{:else}
  <div class="max-w-[820px] space-y-3">
    <Notice tone="error">{failure?.message ?? 'Die Daten konnten nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={onretry}>Erneut laden</Button>
  </div>
{/if}
