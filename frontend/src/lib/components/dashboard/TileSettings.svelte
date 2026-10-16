<script lang="ts">
  import * as Dialog from '$lib/components/ui/dialog';
  import { Button } from '$lib/components/ui/button';
  import {
    DESTINATION_LABELS,
    METRIC_LABELS,
    PERIOD_LABELS,
    WIDTH_LABELS,
    entryOf,
    periodsFor,
  } from '$lib/dashboard/catalog';
  import type { TilePatch } from '$lib/dashboard/draft';
  import {
    DESTINATIONS,
    MAX_NOTE_LENGTH,
    MAX_TITLE_LENGTH,
    METRICS,
    WIDTHS,
    type Destination,
    type DraftSection,
    type DraftTile,
    type Metric,
    type PeriodDays,
    type Width,
  } from '$lib/dashboard/types';

  let {
    open = $bindable(false),
    tile,
    sections,
    zones,
    onPatch,
    onMove,
  }: {
    open: boolean;
    tile: DraftTile | null;
    sections: readonly DraftSection[];
    zones: { id: number; name: string }[];
    onPatch: (key: string, patch: TilePatch, field: string) => void;
    onMove: (key: string, sectionKey: string) => void;
  } = $props();

  const entry = $derived(tile ? entryOf(tile.kind) : null);
  const periods = $derived(tile ? periodsFor(tile.kind) : null);
  const sectionKey = $derived(sections.find((s) => s.tiles.some((t) => t.key === tile?.key))?.key ?? '');
  const field = 'mt-1 block h-10 w-full rounded-lg border bg-background px-2 text-sm';
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="max-h-[90vh] overflow-auto sm:max-w-lg" data-testid="tile-settings">
    {#if tile && entry}
      <Dialog.Header>
        <Dialog.Title>{entry.name} anpassen</Dialog.Title>
        <Dialog.Description>Änderungen gelten im Entwurf und werden erst mit „Speichern“ übernommen.</Dialog.Description>
      </Dialog.Header>
      <div class="grid gap-3">
        <label class="text-sm font-medium">
          Titel
          <input
            class={field}
            type="text"
            maxlength={MAX_TITLE_LENGTH}
            value={tile.title}
            placeholder={entry.name}
            oninput={(e) => onPatch(tile.key, { title: e.currentTarget.value }, 'title')}
          />
          <span class="mt-0.5 block text-xs font-normal text-muted-foreground">Leer lassen für den Standardtitel.</span>
        </label>

        {#if entry.needsZone}
          <label class="text-sm font-medium">
            Raum
            <select class={field} value={tile.zone_id ?? ''} onchange={(e) => onPatch(tile.key, { zone_id: Number(e.currentTarget.value) }, 'zone_id')}>
              {#each zones as z (z.id)}<option value={z.id}>{z.name}</option>{/each}
            </select>
          </label>
        {/if}

        {#if tile.kind === 'metric'}
          <label class="text-sm font-medium">
            Kennzahl
            <select class={field} value={tile.metric ?? 'rooms'} onchange={(e) => onPatch(tile.key, { metric: e.currentTarget.value as Metric }, 'metric')}>
              {#each METRICS as m (m)}<option value={m}>{METRIC_LABELS[m]}</option>{/each}
            </select>
          </label>
        {/if}

        {#if tile.kind === 'shortcut'}
          <label class="text-sm font-medium">
            Ziel
            <select class={field} value={tile.destination ?? 'schedule'} onchange={(e) => onPatch(tile.key, { destination: e.currentTarget.value as Destination }, 'destination')}>
              {#each DESTINATIONS as d (d)}<option value={d}>{DESTINATION_LABELS[d]}</option>{/each}
            </select>
          </label>
        {/if}

        {#if periods}
          <label class="text-sm font-medium">
            Zeitraum
            <select class={field} value={tile.period_days} onchange={(e) => onPatch(tile.key, { period_days: Number(e.currentTarget.value) as PeriodDays }, 'period_days')}>
              {#each periods as p (p)}<option value={p}>{PERIOD_LABELS[p]}</option>{/each}
            </select>
          </label>
        {/if}

        {#if tile.kind === 'note'}
          <label class="text-sm font-medium">
            Text
            <textarea
              class="mt-1 block min-h-24 w-full rounded-lg border bg-background px-2 py-2 text-sm"
              maxlength={MAX_NOTE_LENGTH}
              value={tile.note}
              oninput={(e) => onPatch(tile.key, { note: e.currentTarget.value }, 'note')}
            ></textarea>
            <span class="mt-0.5 block text-right text-xs font-normal text-muted-foreground tabular-nums">{tile.note.length} / {MAX_NOTE_LENGTH} · reiner Text</span>
          </label>
        {/if}

        <label class="text-sm font-medium">
          Breite
          <select class={field} value={tile.width} onchange={(e) => onPatch(tile.key, { width: Number(e.currentTarget.value) as Width }, 'width')}>
            {#each WIDTHS as w (w)}<option value={w}>{WIDTH_LABELS[w]}</option>{/each}
          </select>
        </label>

        <label class="text-sm font-medium">
          Abschnitt
          <select class={field} value={sectionKey} onchange={(e) => onMove(tile.key, e.currentTarget.value)}>
            {#each sections as s (s.key)}<option value={s.key}>{s.title || 'Ohne Titel'}</option>{/each}
          </select>
        </label>
      </div>
      <Dialog.Footer>
        <Dialog.Close>
          {#snippet child({ props })}<Button {...props}>Fertig</Button>{/snippet}
        </Dialog.Close>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
