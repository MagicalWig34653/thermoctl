<script lang="ts">
  import type { VisibleArea, VisiblePage } from '$lib/navigation';
  import Placeholder from '$lib/components/Placeholder.svelte';
  import ModesAdmin from '$lib/components/schedule/ModesAdmin.svelte';
  import ScheduleView from '$lib/components/schedule/ScheduleView.svelte';
  import VacationPanel from '$lib/components/schedule/VacationPanel.svelte';

  let { area, page }: { area: VisibleArea; page: VisiblePage } = $props();
</script>

{#if area.id === 'zeit' && page.id === 'wochenplaene'}
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
