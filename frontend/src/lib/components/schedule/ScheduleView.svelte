<script lang="ts">
  import { CalendarClock } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { scheduleZoneId } from '$lib/schedule/choice';
  import { zoneFromSearch } from '$lib/preselect';
  import AbsencePanel from './AbsencePanel.svelte';
  import ScheduleEditor from './ScheduleEditor.svelte';

  const { router, session } = useApp();
  // Nur Räume, die der Server für dieses Konto sichtbar gemacht hat.
  const zones = $derived(session.bootstrap?.zones ?? []);
  // Raumvorwahl aus der Adresse (`?zone=`): nur ein sichtbarer Raum, sonst still verworfen.
  let chosen = $state<number | null>(zoneFromSearch(router.search, session.bootstrap?.zones ?? []));
  $effect(() => {
    const wanted = zoneFromSearch(router.search, zones);
    if (wanted !== null) chosen = wanted;
  });
  const zoneId = $derived(scheduleZoneId(zones, chosen));
  const tenant = $derived(session.bootstrap?.ui_profile === 'tenant');
</script>

<header class="mb-6">
  <h1>Wochenpläne</h1>
  <p class="text-muted-foreground">Wann soll es in welchem Raum warm sein?</p>
</header>

{#if !zones.length}
  <p class="text-muted-foreground">Keine Räume sichtbar.</p>
{:else}
  <ul class="m-0 mb-6 grid list-none grid-cols-2 gap-2 p-0 sm:grid-cols-3 lg:grid-cols-4" aria-label="Raum wählen">
    {#each zones as zone (zone.id)}
      <li>
        <button
          type="button"
          class="flex min-h-14 w-full items-center gap-2 rounded-xl border bg-card px-4 py-3 text-left font-medium hover:bg-muted aria-pressed:border-foreground aria-pressed:bg-muted"
          aria-pressed={zone.id === zoneId}
          onclick={() => (chosen = zone.id)}
        >
          <CalendarClock size={16} class="shrink-0 text-muted-foreground" aria-hidden="true" />
          <span class="truncate">{zone.name}</span>
        </button>
      </li>
    {/each}
  </ul>

  {#if zoneId !== null}
    {#key zoneId}<ScheduleEditor {zoneId} />{/key}
  {/if}

  {#if tenant}
    <div class="mt-8"><AbsencePanel /></div>
  {/if}
{/if}
