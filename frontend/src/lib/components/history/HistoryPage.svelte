<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { PERIODS, type PeriodId } from '$lib/history/format';
  import HeatingTime from './HeatingTime.svelte';
  import HistoryChart from './HistoryChart.svelte';

  const { session } = useApp();
  const zones = $derived(session.bootstrap?.zones ?? []);

  let period = $state<PeriodId>('7d');
  let zoneId = $state<number | null>(null);
  const shown = $derived(zoneId === null ? zones : zones.filter((z) => z.id === zoneId));
  // Erster geladener Verlauf: für die Messung der Startzeit der Seite.
  let firstLoadMs = $state<number | null>(null);
</script>

<header class="mb-6">
  <h1>Temperatur &amp; Heizzeit</h1>
  <p class="text-muted-foreground">Verlauf der Raumtemperaturen mit Sollwerten und Heizanforderung, dazu die Heizzeit je Raum.</p>
</header>

{#if zones.length === 0}
  <p class="text-muted-foreground">Für dein Konto sind keine Räume sichtbar.</p>
{:else}
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <label class="flex items-center gap-2 text-sm">
      <span class="text-muted-foreground">Raum</span>
      <select
        class="h-9 max-w-[14rem] rounded-lg border bg-background px-2 text-sm"
        value={zoneId ?? ''}
        onchange={(e) => {
          const v = (e.currentTarget as HTMLSelectElement).value;
          zoneId = v === '' ? null : Number(v);
        }}
      >
        <option value="">Alle Räume</option>
        {#each zones as z (z.id)}<option value={z.id}>{z.name}</option>{/each}
      </select>
    </label>
    <div class="flex gap-1" role="group" aria-label="Zeitraum">
      {#each PERIODS as p (p.id)}
        <Button variant={period === p.id ? 'secondary' : 'outline'} size="sm" class="min-h-9" aria-pressed={period === p.id} onclick={() => (period = p.id)}>{p.label}</Button>
      {/each}
    </div>
  </div>

  <div class="space-y-6" data-first-load-ms={firstLoadMs ?? ''}>
    <ul class="m-0 grid list-none gap-4 p-0">
      {#each shown as z, i (z.id)}
        <li class="min-w-0">
          <HistoryChart
            {zones}
            zoneId={z.id}
            {period}
            filters={false}
            lazy={i > 1}
            heading="h2"
            onLoaded={(ms) => {
              if (firstLoadMs === null) firstLoadMs = ms;
            }}
          />
        </li>
      {/each}
    </ul>
    <HeatingTime zoneId={zoneId} />
  </div>
{/if}
