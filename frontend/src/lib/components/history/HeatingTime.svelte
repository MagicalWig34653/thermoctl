<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import HelpTip from '$lib/components/rooms/HelpTip.svelte';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { historyLimiter } from '$lib/history/shared';
  import { createQuery } from '$lib/history/query.svelte';
  import { buildQuery, formatDuration } from '$lib/history/reports';
  import { HEATING_TIME_DISCLAIMER, HEATING_TIME_DRY_RUN, HEATING_TIME_SCOPE } from '$lib/history/wording';
  import type { HeatingTime } from '$lib/history/types';

  let { zoneId = null }: { zoneId?: number | null } = $props();
  const { api } = useApp();

  const PERIODS = [
    { id: '7', label: '7 Tage' },
    { id: '30', label: '30 Tage' },
    { id: '90', label: '90 Tage' },
  ] as const;
  let period = $state<'7' | '30' | '90'>('7');
  const query = createQuery(() =>
    historyLimiter.run(() => api.get<HeatingTime>(`/ui-api/heating-time${buildQuery({ period, zone_id: zoneId })}`)),
  );
  $effect(() => {
    void period;
    void zoneId;
    query.load();
  });
  onMount(() => () => query.cancel());

  const data = $derived(query.data);
  const peak = $derived(Math.max(1, ...(data?.zones.flatMap((z) => z.days.map((d) => d.seconds)) ?? [1])));
  const dayLabel = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}.`;
</script>

<section aria-labelledby="heating-title" class="rounded-xl border bg-card p-4 sm:p-5">
  <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
    <h2 id="heating-title" class="m-0 flex items-center gap-1 text-lg">Heizzeit<HelpTip term="heating_time" /></h2>
    <div class="flex gap-1" role="group" aria-label="Zeitraum der Heizzeit">
      {#each PERIODS as p (p.id)}
        <Button variant={period === p.id ? 'secondary' : 'outline'} size="sm" class="min-h-9" aria-pressed={period === p.id} onclick={() => (period = p.id)}>{p.label}</Button>
      {/each}
    </div>
  </div>

  {#if query.loading && !data}
    <p class="m-0 text-muted-foreground" role="status">Heizzeit wird geladen …</p>
  {:else if !data}
    <div class="space-y-3">
      <Notice tone="error">{query.failure?.message ?? 'Die Heizzeit konnte nicht geladen werden.'}</Notice>
      <Button variant="outline" size="sm" onclick={() => query.load()}>Erneut versuchen</Button>
    </div>
  {:else}
    {#if query.failure}
      <div class="mb-3"><Notice tone="warn">Aktualisieren fehlgeschlagen: {query.failure.message}</Notice></div>
    {/if}
    {#if !data.control_armed}
      <div class="mb-3">
        <Notice tone="warn"><strong>{HEATING_TIME_DRY_RUN.title}</strong> {HEATING_TIME_DRY_RUN.text} Die Heizzeit zählt Entscheidungen der Regelung, es wurde nichts geschaltet.</Notice>
      </div>
    {/if}
    {#if data.zones.length === 0}
      <p class="m-0 text-muted-foreground">Für dein Konto sind keine Räume sichtbar.</p>
    {:else}
      <div class="overflow-x-auto">
        <table class="w-full min-w-[18rem] border-collapse text-sm" data-testid="heating-table">
          <caption class="sr-only">Heizzeit je Raum, letzte {period} Tage, Tage in der Zeitzone {data.timezone}</caption>
          <thead>
            <tr class="text-left text-muted-foreground">
              <th scope="col" class="py-2 pr-3 font-medium">Raum</th>
              <th scope="col" class="py-2 pr-3 font-medium">Gesamt</th>
              <th scope="col" class="py-2 font-medium">Ø pro Tag</th>
            </tr>
          </thead>
          {#each data.zones as z (z.zone_id)}
            <tbody class="border-t">
              <tr>
                <th scope="row" class="pt-2 pr-3 text-left font-medium">{z.zone_name}</th>
                <td class="pt-2 pr-3 whitespace-nowrap tabular-nums">{formatDuration(z.seconds_total)}</td>
                <td class="pt-2 whitespace-nowrap tabular-nums">{formatDuration(z.days.length ? z.seconds_total / z.days.length : 0)}</td>
              </tr>
              <tr>
                <td colspan="3" class="pt-1 pb-2">
                  <div class="flex h-8 items-end gap-px" aria-hidden="true">
                    {#each z.days as d (d.day)}
                      <span class="min-w-px flex-1 rounded-sm bg-[var(--h-heat)]" style="height:{Math.max(d.seconds > 0 ? 6 : 1, (d.seconds / peak) * 100)}%;opacity:{d.seconds > 0 ? 0.75 : 0.25}" title="{dayLabel(d.day)}: {formatDuration(d.seconds)}"></span>
                    {/each}
                  </div>
                  <details class="mt-1 text-xs">
                    <summary class="cursor-pointer text-muted-foreground">Tage anzeigen</summary>
                    <table class="mt-1 border-collapse tabular-nums">
                      <caption class="sr-only">Heizzeit {z.zone_name} je Tag</caption>
                      <thead><tr><th scope="col" class="pr-3 text-left font-medium">Tag</th><th scope="col" class="text-left font-medium">Heizzeit</th></tr></thead>
                      <tbody>
                        {#each z.days as d (d.day)}
                          <tr><th scope="row" class="pr-3 text-left font-normal">{dayLabel(d.day)}</th><td>{formatDuration(d.seconds)}</td></tr>
                        {/each}
                      </tbody>
                    </table>
                  </details>
                </td>
              </tr>
            </tbody>
          {/each}
        </table>
      </div>
      <p class="m-0 mt-3 text-xs text-muted-foreground">
        Tage ohne Messzeilen erscheinen als 0 und sind von „nicht geheizt“ nicht zu unterscheiden.
      </p>
      <p class="m-0 mt-1 text-xs text-muted-foreground" data-testid="heating-disclaimer">{HEATING_TIME_SCOPE} {HEATING_TIME_DISCLAIMER}</p>
    {/if}
  {/if}
</section>
