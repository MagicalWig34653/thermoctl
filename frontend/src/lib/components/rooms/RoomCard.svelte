<script lang="ts">
  import { TriangleAlert, Flame, Clock, Wind } from '@lucide/svelte';
  import { Badge } from '$lib/components/ui/badge';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { formatCelsius, formatTemperature } from '$lib/rooms/decimal';
  import { formatInstant, measurementLine, plannedDiffers, roomStatus } from '$lib/rooms/logic';
  import type { Room } from '$lib/rooms/types';
  import DayTrack from './DayTrack.svelte';

  let { room, timezone, nowFraction, heading }: { room: Room; timezone: string; nowFraction: number; heading?: string } = $props();
  const { prefix, session } = useApp();
  const tenant = $derived(session.bootstrap?.ui_profile === 'tenant');
  const status = $derived(roomStatus(room, tenant));
  const line = $derived(measurementLine(room, tenant));
  const differs = $derived(plannedDiffers(room));
</script>

<a
  href={appHref(prefix, `/raeume/${room.id}`)}
  class="group @container flex h-full min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/60"
  data-testid="room-card"
  aria-label={`${heading ?? room.name}, Details öffnen`}
>
  <div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
    <h3 class="max-w-full min-w-0 truncate text-base" title={heading ?? room.name}>{heading ?? room.name}</h3>
    <Badge variant={status.attention ? 'destructive' : status.kind === 'heating' ? 'default' : 'secondary'} class="h-6 gap-1">
      {#if status.kind === 'heating'}<Flame aria-hidden="true" />{:else if status.kind === 'window'}<Wind aria-hidden="true" />{:else if status.attention}<TriangleAlert aria-hidden="true" />{/if}
      {status.label}
    </Badge>
  </div>

  <div class="flex flex-wrap items-end justify-between gap-x-3 gap-y-2">
    <div class="min-w-0">
      <div class="text-3xl leading-none font-semibold tracking-tight tabular-nums @[15rem]:text-4xl" aria-label={`Ist ${formatCelsius(room.temperature_c)}`}>
        {formatTemperature(room.temperature_c)}<span class="ml-1 text-lg font-normal text-muted-foreground">°C</span>
      </div>
      <div class="mt-1 text-xs {line.problem ? 'text-destructive' : 'text-muted-foreground'}" data-testid="measurement-line">{line.text}</div>
    </div>
    <div class="ml-auto text-right">
      <div class="text-xs text-muted-foreground">Soll</div>
      <div class="text-xl font-semibold tabular-nums">{formatCelsius(room.setpoint.temperature_c)}</div>
      {#if differs}
        <div class="text-xs text-muted-foreground">Plan: {formatCelsius(room.decision?.planned_setpoint_c)}</div>
      {/if}
    </div>
  </div>

  <div class="text-sm text-muted-foreground">
    {#if room.override}
      <span class="inline-flex items-center gap-1 font-medium text-foreground"><Clock size={14} aria-hidden="true" />Übersteuert</span>
      {room.override.ends_at ? `bis ${formatInstant(room.override.ends_at, timezone)}` : 'ohne Ende'}
    {:else if room.operating_mode.code !== 'auto'}
      {room.operating_mode.label}
    {:else}
      {room.setpoint.mode_name ?? room.setpoint.reason}
    {/if}
  </div>

  <DayTrack segments={room.day_track} {nowFraction} compact />

  {#if room.emergency}
    <div class="flex items-center gap-1.5 text-xs text-destructive">
      <TriangleAlert size={14} aria-hidden="true" />{room.emergency.headline}
    </div>
  {/if}
</a>
