<script lang="ts">
  import type { DaySegment } from '$lib/rooms/types';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatMinute, nowPercent, segmentBox } from '$lib/rooms/logic';

  let {
    segments,
    nowFraction,
    compact = false,
  }: { segments: DaySegment[]; nowFraction: number; compact?: boolean } = $props();
  const now = $derived(nowPercent(nowFraction));
</script>

{#if segments.length}
  <div class="relative w-full {compact ? 'h-2' : 'h-4'}">
  <!-- Leuchten: unscharfe Kopie der Abschnitte hinter dem Balken (nur mit Glow sichtbar). -->
  <div class="day-glow pointer-events-none absolute inset-0" aria-hidden="true">
    {#each segments as segment, i (i)}
      {@const box = segmentBox(segment)}
      <span class="absolute inset-y-0" style="left:{box.left}%;width:{box.width}%;background:color-mix(in oklch, var(--chart-2) {Math.round(segment.warmth * 100)}%, var(--chart-1))"></span>
    {/each}
  </div>
  <div
    class="relative h-full w-full overflow-hidden rounded-md bg-muted"
    role="img"
    aria-label={`Tagesverlauf: ${segments
      .map((s) => `${formatMinute(s.start_minute)} bis ${formatMinute(s.end_minute)} ${s.mode_name}${s.temperature_c ? ' ' + formatCelsius(s.temperature_c) : ''}`)
      .join(', ')}`}
  >
    {#each segments as segment, i (i)}
      {@const box = segmentBox(segment)}
      <span
        class="absolute inset-y-0"
        style="left:{box.left}%;width:{box.width}%;background:color-mix(in oklch, var(--chart-2) {Math.round(
          segment.warmth * 100,
        )}%, var(--chart-1));opacity:.85"
        title={`${formatMinute(segment.start_minute)}–${formatMinute(segment.end_minute)} · ${segment.mode_name}`}
      ></span>
    {/each}
    <span class="absolute inset-y-0 w-0.5 bg-foreground" style="left:calc({now}% - 1px)" aria-hidden="true"></span>
  </div>
  </div>
{:else}
  <p class="m-0 text-xs text-muted-foreground">Kein Plan für heute.</p>
{/if}
