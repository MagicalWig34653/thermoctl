<script lang="ts">
  import type { TileKind } from '$lib/dashboard/types';

  // Schematische Vorschau je Kachelart: keine Daten, nur die Form. Für Hilfsmittel unsichtbar,
  // der Name und die Beschreibung der Karte tragen die Information.
  let { kind }: { kind: TileKind } = $props();
</script>

<div class="pointer-events-none h-24 overflow-hidden rounded-lg border bg-background p-2" aria-hidden="true" data-testid={`preview-${kind}`}>
  {#if kind === 'room'}
    <div class="flex items-end justify-between"><span class="text-2xl leading-none font-semibold tabular-nums">21,5°</span><span class="text-xs text-muted-foreground">Soll 21,0</span></div>
    <div class="mt-2 flex h-2 overflow-hidden rounded-full"><span class="w-1/4 bg-blue-400/70"></span><span class="w-2/4 bg-orange-400/70"></span><span class="w-1/4 bg-blue-400/70"></span></div>
    <div class="mt-2 h-2 w-1/2 rounded bg-muted"></div>
  {:else if kind === 'chart'}
    <svg viewBox="0 0 120 60" class="h-full w-full" preserveAspectRatio="none"><polyline points="0,40 15,38 30,30 45,34 60,22 75,26 90,18 105,24 120,16" fill="none" stroke="var(--h-temp)" stroke-width="2" /><polyline points="0,44 40,44 41,28 80,28 81,36 120,36" fill="none" stroke="var(--h-sched)" stroke-width="1.5" stroke-dasharray="3 2" /></svg>
  {:else if kind === 'metric'}
    <div class="text-4xl leading-none font-semibold tabular-nums">4</div>
    <div class="mt-2 h-2 w-2/3 rounded bg-muted"></div>
  {:else if kind === 'agenda'}
    {#each [0, 1, 2] as i (i)}<div class="mb-1.5 flex items-center gap-2"><span class="size-3 rounded-full bg-amber-500/70"></span><span class="h-2 flex-1 rounded bg-muted"></span></div>{/each}
  {:else if kind === 'schedule'}
    {#each [0, 1, 2] as i (i)}<div class="mb-1.5 flex items-center justify-between gap-2"><span class="text-xs tabular-nums text-muted-foreground">{['06:30', '17:00', '22:00'][i]}</span><span class="h-2 w-1/2 rounded bg-muted"></span></div>{/each}
  {:else if kind === 'heating'}
    <div class="text-2xl leading-none font-semibold tabular-nums">5 Std. 20 Min.</div>
    <div class="mt-2 flex h-8 items-end gap-px">{#each [4, 7, 3, 8, 6, 2, 5] as h, i (i)}<span class="flex-1 rounded-sm bg-[var(--h-heat)]/70" style="height:{h * 11}%"></span>{/each}</div>
  {:else if kind === 'devices'}
    {#each [0, 1, 2] as i (i)}<div class="mb-1.5 flex items-center justify-between gap-2"><span class="h-2 w-1/2 rounded bg-muted"></span><span class="h-2 w-1/5 rounded bg-emerald-500/60"></span></div>{/each}
  {:else if kind === 'system'}
    {#each [0, 1, 2] as i (i)}<div class="mb-1 flex items-center gap-2 rounded bg-muted/70 px-2 py-1"><span class="size-2 rounded-full {['bg-emerald-600', 'bg-amber-500', 'bg-muted-foreground'][i]}"></span><span class="h-1.5 flex-1 rounded bg-background"></span></div>{/each}
  {:else if kind === 'note'}
    <div class="mb-1.5 h-2 w-full rounded bg-muted"></div><div class="mb-1.5 h-2 w-5/6 rounded bg-muted"></div><div class="h-2 w-1/2 rounded bg-muted"></div>
  {:else}
    <div class="mb-2 h-2 w-1/2 rounded bg-muted"></div><div class="inline-block rounded-md border px-3 py-1 text-xs">Seite öffnen ↗</div>
  {/if}
</div>
