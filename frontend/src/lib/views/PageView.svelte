<script lang="ts">
  import type { VisibleArea, VisiblePage } from '$lib/navigation';
  import Lazy from '$lib/components/Lazy.svelte';
  import NotFound from '$lib/components/NotFound.svelte';
  import { PAGE_LOADERS } from './pages';

  let { area, page }: { area: VisibleArea; page: VisiblePage } = $props();
  // Die Sichtbarkeit kommt unverändert aus dem Server (`page` existiert nur, wenn er einen der Pfade
  // geliefert hat), jeder Endpunkt prüft erneut. Der Code der Seite wird erst jetzt geladen.
  const key = $derived(`${area.id}/${page.id}`);
  const loader = $derived(PAGE_LOADERS[key]);
</script>

{#if loader}
  {#key key}
    <Lazy load={loader} props={{ page, title: page.label }} />
  {/key}
{:else}
  <NotFound />
{/if}
