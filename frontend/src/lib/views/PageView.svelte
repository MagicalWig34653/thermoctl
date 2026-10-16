<script lang="ts">
  import type { VisibleArea, VisiblePage } from '$lib/navigation';
  import KioskPage from '$lib/components/access/KioskPage.svelte';
  import PeoplePage from '$lib/components/access/PeoplePage.svelte';
  import TokensPage from '$lib/components/access/TokensPage.svelte';
  import Placeholder from '$lib/components/Placeholder.svelte';
  import ControllersPage from '$lib/components/devices/ControllersPage.svelte';
  import DevicesPage from '$lib/components/devices/DevicesPage.svelte';
  import MapPage from '$lib/components/devices/MapPage.svelte';
  import EventsPage from '$lib/components/history/EventsPage.svelte';
  import HistoryPage from '$lib/components/history/HistoryPage.svelte';
  import WearPage from '$lib/components/history/WearPage.svelte';
  import ModesAdmin from '$lib/components/schedule/ModesAdmin.svelte';
  import ScheduleView from '$lib/components/schedule/ScheduleView.svelte';
  import VacationPanel from '$lib/components/schedule/VacationPanel.svelte';

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
{:else if key === 'geraete/liste'}
  <DevicesPage />
{:else if key === 'geraete/bediengeraete'}
  <ControllersPage />
{:else if key === 'geraete/karte'}
  <MapPage />
{:else if key === 'personen/rechte'}
  <PeoplePage {page} />
{:else if key === 'personen/tokens'}
  <TokensPage title={page.label} />
{:else if key === 'personen/kiosk'}
  <KioskPage title={page.label} />
{:else if area.id === 'zeit' && page.id === 'wochenplaene'}
  <ScheduleView />
{:else if area.id === 'zeit' && page.id === 'anlagenurlaub'}
  <header class="mb-6">
    <h1>{page.label}</h1>
    <p class="text-muted-foreground">Urlaub der gesamten Anlage.</p>
  </header>
  <VacationPanel />
{:else if area.id === 'heizung' && page.id === 'modi'}
  <header class="mb-6">
    <h1>{page.label}</h1>
    <p class="text-muted-foreground">Namen und Reihenfolge der Modi in den Wochenplänen.</p>
  </header>
  <ModesAdmin />
{:else}
  <Placeholder title={page.label} description={area.label} links={page.links} />
{/if}
