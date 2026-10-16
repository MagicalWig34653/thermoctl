<script lang="ts">
  import { onMount } from 'svelte';
  import { CalendarClock } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { createResource } from '$lib/rooms/resource.svelte';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { localClock } from '$lib/schedule/format';
  import type { Preview } from '$lib/schedule/types';
  import { historyLimiter } from '$lib/history/shared';
  import { appHref } from '$lib/paths';
  import { DESTINATION_PATHS } from '$lib/dashboard/catalog';
  import TileCard from './TileCard.svelte';

  let { zoneId, roomName, title }: { zoneId: number; roomName: string; title: string } = $props();
  const { api, prefix } = useApp();
  // Der Server liefert die kommenden 24 Stunden in der Rangfolge der Regelung; hier wird nur gelistet.
  const preview = createResource(() => historyLimiter.run(() => api.get<Preview>(`/ui-api/schedule/${zoneId}/preview`)));
  onMount(() => preview.start());
  const data = $derived(preview.data);
  const next = $derived(data ? data.segments.slice(1, 6) : []);
  const current = $derived(data?.segments[0] ?? null);
</script>

<TileCard {title} subtitle={roomName} icon={CalendarClock} testid="tile-schedule">
  {#if !data && preview.failure}
    <p class="m-0 text-sm text-destructive" role="alert">{preview.failure.message}</p>
  {:else if !data}
    <p class="m-0 text-sm text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if current}
      <p class="m-0 text-sm">Jetzt <strong class="tabular-nums">{formatCelsius(current.temperature_c)}</strong>{current.mode_name ? ` · ${current.mode_name}` : ''}</p>
    {/if}
    {#if next.length === 0}
      <p class="m-0 text-sm text-muted-foreground">In den nächsten {data.horizon_hours} Stunden steht kein Wechsel an.</p>
    {:else}
      <ul class="m-0 grid list-none gap-1 p-0 text-sm">
        {#each next as s (s.starts_at)}
          <li class="flex items-baseline justify-between gap-2 border-t pt-1">
            <span class="tabular-nums">ab {localClock(s.local_starts_at)} Uhr</span>
            <span class="min-w-0 truncate text-muted-foreground">{s.mode_name ?? s.reason} · <span class="tabular-nums text-foreground">{formatCelsius(s.temperature_c)}</span></span>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
  <a class="mt-auto text-sm font-medium underline-offset-4 hover:underline" href={appHref(prefix, DESTINATION_PATHS.schedule)}>Wochenplan öffnen</a>
</TileCard>
