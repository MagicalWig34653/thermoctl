<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { PERIODS, type PeriodId } from '$lib/history/format';
  import { periodFromSearch, zoneFromSearch } from '$lib/preselect';
  import { historyWording } from '$lib/history/wording';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import HeatingTime from './HeatingTime.svelte';
  import HistoryChart from './HistoryChart.svelte';

  const { router, session } = useApp();
  const zones = $derived(session.bootstrap?.zones ?? []);
  const tenant = $derived(session.bootstrap?.ui_profile === 'tenant');
  const w = $derived(historyWording(tenant));
  // Ob die Anlage scharf ist, meldet der Verlauf selbst (`control_armed`); der erste geladene genügt.
  let armed = $state<boolean | null>(null);

  // Vorwahl aus der Adresse (`?zone=`, `?period=`): nur sichtbare Räume und bekannte Zeiträume.
  let period = $state<PeriodId>(periodFromSearch(router.search) ?? '7d');
  let zoneId = $state<number | null>(zoneFromSearch(router.search, session.bootstrap?.zones ?? []));
  $effect(() => {
    const wantedZone = zoneFromSearch(router.search, zones);
    if (wantedZone !== null) zoneId = wantedZone;
    const wantedPeriod = periodFromSearch(router.search);
    if (wantedPeriod !== null) period = wantedPeriod;
  });
  const shown = $derived(zoneId === null ? zones : zones.filter((z) => z.id === zoneId));
  // Erster geladener Verlauf: für die Messung der Startzeit der Seite.
  let firstLoadMs = $state<number | null>(null);
</script>

<header class="mb-6">
  <h1>Temperatur &amp; Heizzeit</h1>
  <p class="text-muted-foreground">Verlauf der Raumtemperaturen mit Sollwerten und Heizanforderung, dazu die Heizzeit je Raum.</p>
</header>

{#if armed === false}
  <div class="mb-4 max-w-3xl" data-testid="history-dry-run">
    <Notice tone="warn"><strong>{w.dryRun.title}</strong> {w.dryRun.text}</Notice>
  </div>
{/if}

{#if w.help}
  <section class="mb-4 max-w-3xl text-sm text-muted-foreground" aria-label="So liest du den Verlauf" data-testid="history-help">
    <p class="mt-0">{w.help.intro}</p>
    <ul class="m-0 grid list-disc gap-1 pl-5">
      {#each w.help.bullets as line (line)}<li>{line}</li>{/each}
    </ul>
  </section>
{/if}

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
            onLoaded={(ms, controlArmed) => {
              if (firstLoadMs === null) firstLoadMs = ms;
              armed = controlArmed;
            }}
          />
        </li>
      {/each}
    </ul>
    <HeatingTime zoneId={zoneId} />
  </div>
{/if}
