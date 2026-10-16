<script lang="ts">
  import type { VisibleArea, VisiblePage } from '$lib/navigation';
  import Placeholder from '$lib/components/Placeholder.svelte';
  import EventsPage from '$lib/components/history/EventsPage.svelte';
  import HistoryPage from '$lib/components/history/HistoryPage.svelte';
  import WearPage from '$lib/components/history/WearPage.svelte';

  let { area, page }: { area: VisibleArea; page: VisiblePage } = $props();
  // Die Seiten der Auswertung sind migriert; die Sichtbarkeit kommt unverändert aus dem Server
  // (`page` existiert nur, wenn er einen der Pfade geliefert hat), jeder Endpunkt prüft erneut.
  const key = $derived(`${area.id}/${page.id}`);
</script>

{#if key === 'auswertung/verlauf'}
  <HistoryPage />
{:else if key === 'auswertung/ereignisse'}
  <EventsPage />
{:else if key === 'auswertung/verschleiss'}
  <WearPage />
{:else}
  <Placeholder title={page.label} description={area.label} links={page.links} />
{/if}
