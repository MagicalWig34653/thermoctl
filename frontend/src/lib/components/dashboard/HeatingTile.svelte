<script lang="ts">
  import { onMount } from 'svelte';
  import { Flame } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { createResource } from '$lib/rooms/resource.svelte';
  import { historyLimiter } from '$lib/history/shared';
  import { formatDuration } from '$lib/history/reports';
  import type { HeatingTime } from '$lib/history/types';
  import { appHref } from '$lib/paths';
  import { DESTINATION_PATHS } from '$lib/dashboard/catalog';
  import TileCard from './TileCard.svelte';

  let { zoneId, roomName, title, days }: { zoneId: number; roomName: string; title: string; days: number } = $props();
  const { api, prefix } = useApp();
  const heating = createResource(() =>
    historyLimiter.run(() => api.get<HeatingTime>(`/ui-api/heating-time?period=${days}&zone_id=${zoneId}`)),
  );
  onMount(() => heating.start());
  const data = $derived(heating.data);
  const zone = $derived(data?.zones.find((z) => z.zone_id === zoneId) ?? null);
  const peak = $derived(Math.max(1, ...(zone?.days.map((d) => d.seconds) ?? [1])));
</script>

<TileCard {title} subtitle={`${roomName} · ${days} Tage`} icon={Flame} testid="tile-heating">
  {#if !data && heating.failure}
    <p class="m-0 text-sm text-destructive" role="alert">{heating.failure.message}</p>
  {:else if !data}
    <p class="m-0 text-sm text-muted-foreground" role="status">Wird geladen …</p>
  {:else if !zone}
    <p class="m-0 text-sm text-muted-foreground">Keine Daten für diesen Zeitraum.</p>
  {:else}
    <div class="text-3xl leading-none font-semibold tabular-nums" aria-label={`Heizzeit gesamt ${formatDuration(zone.seconds_total)}`}>{formatDuration(zone.seconds_total)}</div>
    <p class="m-0 text-xs text-muted-foreground">
      Ø {formatDuration(zone.days.length ? zone.seconds_total / zone.days.length : 0)} pro Tag{data.control_armed ? '' : ' · Entscheidungen der Regelung im Trockenlauf'}
    </p>
    <div class="flex h-8 items-end gap-px" aria-hidden="true">
      {#each zone.days as d (d.day)}
        <span class="min-w-px flex-1 rounded-sm bg-[var(--h-heat)]" style="height:{Math.max(d.seconds > 0 ? 6 : 1, (d.seconds / peak) * 100)}%;opacity:{d.seconds > 0 ? 0.75 : 0.25}"></span>
      {/each}
    </div>
  {/if}
  <a class="mt-auto text-sm font-medium underline-offset-4 hover:underline" href={appHref(prefix, DESTINATION_PATHS.history)}>Zur Auswertung</a>
</TileCard>
