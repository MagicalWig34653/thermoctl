<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import HelpTip from '$lib/components/rooms/HelpTip.svelte';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createQuery } from '$lib/history/query.svelte';
  import { buildQuery, wearLabel } from '$lib/history/reports';
  import type { RelayWear } from '$lib/history/types';

  const { api } = useApp();
  const PERIODS = [
    { id: '7', label: '7 Tage' },
    { id: '30', label: '30 Tage' },
    { id: '90', label: '90 Tage' },
  ] as const;
  let period = $state<'7' | '30' | '90'>('30');
  const query = createQuery(() => api.get<RelayWear>(`/ui-api/relay-wear${buildQuery({ period })}`));
  $effect(() => {
    void period;
    query.load();
  });
  onMount(() => () => query.cancel());
  const data = $derived(query.data);
  const nf = new Intl.NumberFormat('de-DE');
  const pf = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 });
  const tone = { ok: 'bg-muted', warn: 'bg-amber-500/20', high: 'bg-destructive/20 text-destructive' } as const;
</script>

<header class="mb-6">
  <h1 class="flex items-center gap-1">Relaisverschleiß<HelpTip term="relay_wear" /></h1>
  <p class="text-muted-foreground">Bestätigte Schaltwechsel je Gerät, hochgerechnet aufs Jahr.</p>
</header>

<div class="mb-4 flex gap-1" role="group" aria-label="Zeitraum">
  {#each PERIODS as p (p.id)}
    <Button variant={period === p.id ? 'secondary' : 'outline'} size="sm" class="min-h-9" aria-pressed={period === p.id} onclick={() => (period = p.id)}>{p.label}</Button>
  {/each}
</div>

{#if query.loading && !data}
  <p class="text-muted-foreground" role="status">Verschleiß wird geladen …</p>
{:else if !data}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{query.failure?.message ?? 'Der Verschleiß konnte nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => query.load()}>Erneut versuchen</Button>
  </div>
{:else}
  {#if query.failure}<div class="mb-3"><Notice tone="warn">Aktualisieren fehlgeschlagen: {query.failure.message}</Notice></div>{/if}
  {#if data.devices.length === 0}
    <p class="text-muted-foreground" role="status">Keine geschalteten Geräte im Zeitraum.</p>
  {:else}
    <div class="overflow-x-auto rounded-xl border">
      <table class="w-full min-w-[40rem] border-collapse text-sm" data-testid="wear-table">
        <caption class="sr-only">Relaisverschleiß, letzte {period} Tage; angenommene Lebensdauer {nf.format(data.assumed_lifetime_operations)} Schaltwechsel</caption>
        <thead>
          <tr class="bg-muted/50 text-left">
            <th scope="col" class="px-3 py-2 font-medium">Gerät</th>
            <th scope="col" class="px-3 py-2 font-medium">Raum</th>
            <th scope="col" class="px-3 py-2 text-right font-medium">Schaltwechsel</th>
            <th scope="col" class="px-3 py-2 text-right font-medium">Pro Jahr (geschätzt)</th>
            <th scope="col" class="px-3 py-2 text-right font-medium">Lebensdauer</th>
            <th scope="col" class="px-3 py-2 font-medium">Stufe</th>
          </tr>
        </thead>
        <tbody>
          {#each data.devices as d (`${d.zone_id}-${d.device_name}`)}
            {@const w = wearLabel(d.wear_level)}
            <tr class="border-t">
              <th scope="row" class="px-3 py-2 text-left font-medium">{d.device_name}</th>
              <td class="px-3 py-2">{d.zone_name}</td>
              <td class="px-3 py-2 text-right tabular-nums">{nf.format(d.operations_total)}</td>
              <td class="px-3 py-2 text-right tabular-nums">{nf.format(d.annual_projection)}</td>
              <td class="px-3 py-2 text-right tabular-nums">{d.lifetime_years === null ? '—' : `${pf.format(d.lifetime_years)} Jahre`}</td>
              <td class="px-3 py-2"><span class="rounded-md px-1.5 py-0.5 text-xs {tone[w.tone]}">{w.label}</span></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="mt-3 text-xs text-muted-foreground">
      Annahme: {nf.format(data.assumed_lifetime_operations)} Schaltwechsel Lebensdauer je Relais. Das ist eine Schätzung, keine Herstellerangabe.
    </p>
  {/if}
{/if}
