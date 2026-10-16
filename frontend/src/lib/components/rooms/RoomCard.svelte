<script lang="ts">
  import { TriangleAlert, Flame, Clock, Wind } from '@lucide/svelte';
  import { Badge } from '$lib/components/ui/badge';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { formatCelsius, formatTemperature } from '$lib/rooms/decimal';
  import { formatAge, formatInstant, plannedDiffers, roomStatus } from '$lib/rooms/logic';
  import type { Room } from '$lib/rooms/types';
  import DayTrack from './DayTrack.svelte';

  let { room, timezone, nowFraction }: { room: Room; timezone: string; nowFraction: number } = $props();
  const { prefix } = useApp();
  const status = $derived(roomStatus(room));
  const differs = $derived(plannedDiffers(room));
  const sensorProblem = $derived(room.sensor.status_code !== 'ok' || room.temperature_c === null);
</script>

<a
  href={appHref(prefix, `/raeume/${room.id}`)}
  class="group flex h-full flex-col gap-3 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/60"
  data-testid="room-card"
  aria-label={`${room.name}, Details öffnen`}
>
  <div class="flex items-start justify-between gap-3">
    <h3 class="min-w-0 truncate text-base" title={room.name}>{room.name}</h3>
    <Badge variant={status.attention ? 'destructive' : status.kind === 'heating' ? 'default' : 'secondary'} class="h-6 gap-1">
      {#if status.kind === 'heating'}<Flame aria-hidden="true" />{:else if status.kind === 'window'}<Wind aria-hidden="true" />{:else if status.attention}<TriangleAlert aria-hidden="true" />{/if}
      {status.label}
    </Badge>
  </div>

  <div class="flex items-end justify-between gap-3">
    <div>
      <div class="text-4xl leading-none font-semibold tracking-tight tabular-nums" aria-label={`Ist ${formatCelsius(room.temperature_c)}`}>
        {formatTemperature(room.temperature_c)}<span class="ml-1 text-lg font-normal text-muted-foreground">°C</span>
      </div>
      <div class="mt-1 text-xs {sensorProblem ? 'text-destructive' : 'text-muted-foreground'}">
        {#if room.temperature_c === null}{room.sensor.status_label}{:else}{room.sensor.status_code === 'ok' ? 'Messung' : room.sensor.status_label} {formatAge(room.measured_age_seconds)}{/if}
      </div>
    </div>
    <div class="text-right">
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
