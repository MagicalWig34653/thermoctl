<script lang="ts">
  import { useApp } from '$lib/context';
  import { zoneFromSearch } from '$lib/preselect';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import SurveyList from '$lib/components/devices/SurveyList.svelte';
  import ZoneAssignment from '$lib/components/devices/ZoneAssignment.svelte';

  const { router, session } = useApp();
  type Tab = 'geraete' | 'raeume';
  // Mit `?zone=` einer sichtbaren Raums öffnet die Seite gleich dessen Zuordnung.
  const wanted = zoneFromSearch(router.search, session.bootstrap?.zones ?? []);
  let tab = $state<Tab>(wanted === null ? 'geraete' : 'raeume');
  let zoneId = $state<number | null>(wanted);
  const zones = $derived(session.bootstrap?.zones ?? []);
  $effect(() => {
    const next = zoneFromSearch(router.search, zones);
    if (next !== null) {
      zoneId = next;
      tab = 'raeume';
    }
  });
</script>

<header class="mb-6">
  <h1>Geräte & Zuordnung</h1>
  <p class="text-muted-foreground">Welche Geräte es gibt, wie es ihnen geht und zu welchem Raum sie gehören.</p>
</header>

<div class="mb-4 flex flex-wrap gap-1" role="group" aria-label="Bereich">
  <Button variant={tab === 'geraete' ? 'secondary' : 'outline'} aria-pressed={tab === 'geraete'} onclick={() => (tab = 'geraete')}>Geräte</Button>
  <Button variant={tab === 'raeume' ? 'secondary' : 'outline'} aria-pressed={tab === 'raeume'} onclick={() => (tab = 'raeume')}>Zuordnung je Raum</Button>
</div>

{#if tab === 'geraete'}
  <SurveyList />
{:else}
  <section aria-labelledby="rooms-title" class="mb-6">
    <h2 id="rooms-title" class="m-0 text-base">Raum wählen</h2>
    {#if zones.length}
      <ul class="m-0 mt-2 grid list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-4">
        {#each zones as z (z.id)}
          <li>
            <button
              type="button"
              class="flex min-h-14 w-full items-center rounded-xl border px-4 py-3 text-left font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 {zoneId === z.id ? 'border-foreground bg-muted' : 'bg-card'}"
              aria-pressed={zoneId === z.id}
              onclick={() => (zoneId = z.id)}>{z.name}</button
            >
          </li>
        {/each}
      </ul>
    {:else}
      <Notice tone="info">Keine Räume sichtbar.</Notice>
    {/if}
  </section>
  {#if zoneId !== null}
    {#key zoneId}<ZoneAssignment {zoneId} />{/key}
  {:else if zones.length}
    <p class="text-muted-foreground">Wähle einen Raum, um seine Geräte zu sehen.</p>
  {/if}
{/if}
