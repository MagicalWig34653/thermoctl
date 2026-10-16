<script lang="ts">
  import { Plus, Search } from '@lucide/svelte';
  import * as Dialog from '$lib/components/ui/dialog';
  import { Button } from '$lib/components/ui/button';
  import { CATALOG, CATEGORIES, WIDTH_LABELS, matchesQuery, type CatalogEntry, type CategoryId } from '$lib/dashboard/catalog';
  import { canAddSection, canAddTile } from '$lib/dashboard/draft';
  import { MAX_SECTIONS, MAX_TILES_PER_SECTION, type DraftSection, type TileKind } from '$lib/dashboard/types';
  import TilePreview from './TilePreview.svelte';

  const NEW = '__new__';

  let {
    open = $bindable(false),
    sections,
    zones,
    admin,
    targetKey = $bindable(''),
    onAdd,
  }: {
    open: boolean;
    sections: readonly DraftSection[];
    zones: { id: number; name: string }[];
    admin: boolean;
    targetKey: string;
    /** Fügt hinzu und liefert die Ansage („… in Dein Tag als Kachel 3“) oder null bei Ablehnung. */
    onAdd: (kind: TileKind, sectionKey: string | 'new', zoneId: number | null) => string | null;
  } = $props();

  let query = $state('');
  let category = $state<CategoryId | 'all'>('all');
  let zoneId = $state<number | null>(null);
  let hovered = $state<TileKind | null>(null);
  let message = $state<string | null>(null);

  const zone = $derived(zoneId !== null && zones.some((z) => z.id === zoneId) ? zoneId : (zones[0]?.id ?? null));
  const effectiveTarget = $derived(targetKey === NEW || sections.some((s) => s.key === targetKey) ? targetKey : (sections[0]?.key ?? NEW));
  const target = $derived(effectiveTarget === NEW ? null : (sections.find((s) => s.key === effectiveTarget) ?? null));
  const shown = $derived(
    CATALOG.filter((e) => (category === 'all' || e.category === category) && matchesQuery(e, query) && (admin || !e.adminOnly)),
  );
  const slotEntry = $derived<CatalogEntry>(CATALOG.find((e) => e.kind === hovered) ?? CATALOG[0]);
  const full = $derived(target ? !canAddTile(sections, target.key) : !canAddSection(sections));
  const landing = $derived(
    target
      ? `Landet am Ende von „${target.title || 'Ohne Titel'}“ als Kachel ${target.tiles.length + 1}.`
      : `Landet in einem neuen Abschnitt (Abschnitt ${sections.length + 1}).`,
  );

  function add(entry: CatalogEntry) {
    const result = onAdd(entry.kind, effectiveTarget === NEW ? 'new' : effectiveTarget, entry.needsZone ? zone : null);
    message = result ?? (full ? (target ? `Dieser Abschnitt ist voll (${MAX_TILES_PER_SECTION} Kacheln).` : `Es sind höchstens ${MAX_SECTIONS} Abschnitte möglich.`) : 'Das ließ sich nicht hinzufügen.');
  }
  const field = 'mt-1 block h-10 w-full rounded-lg border bg-background px-2 text-sm';
</script>

<Dialog.Root bind:open onOpenChange={(v) => { if (!v) message = null; }}>
  <Dialog.Content class="dash-gallery max-h-[92vh] w-[calc(100%-1.5rem)] overflow-auto sm:max-w-5xl" data-testid="gallery">
    <Dialog.Header>
      <Dialog.Title>Kachel hinzufügen</Dialog.Title>
      <Dialog.Description>Wähle eine Kachel, den Abschnitt und gegebenenfalls den Raum. Die Kachel erscheint sofort im Entwurf.</Dialog.Description>
    </Dialog.Header>

    <div class="grid gap-4 md:grid-cols-[13rem_minmax(0,1fr)]">
      <div class="grid content-start gap-3">
        <div class="relative">
          <Search size={16} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input type="search" bind:value={query} placeholder="Kachel suchen" aria-label="Kacheln durchsuchen" class="h-10 w-full rounded-lg border bg-background pr-3 pl-9 text-sm" />
        </div>
        <div class="flex flex-wrap gap-1 md:flex-col" role="group" aria-label="Kategorie">
          <Button variant={category === 'all' ? 'secondary' : 'ghost'} size="sm" class="min-h-9 justify-start" aria-pressed={category === 'all'} onclick={() => (category = 'all')}>Alle</Button>
          {#each CATEGORIES as c (c.id)}
            <Button variant={category === c.id ? 'secondary' : 'ghost'} size="sm" class="min-h-9 justify-start" aria-pressed={category === c.id} onclick={() => (category = c.id)}>{c.label}</Button>
          {/each}
        </div>
        <label class="text-sm font-medium">
          Abschnitt
          <select class={field} value={effectiveTarget} onchange={(e) => (targetKey = e.currentTarget.value)} data-testid="gallery-section">
            {#each sections as s (s.key)}<option value={s.key}>{s.title || 'Ohne Titel'} ({s.tiles.length})</option>{/each}
            <option value={NEW}>Neuer Abschnitt</option>
          </select>
        </label>
        {#if zones.length}
          <label class="text-sm font-medium">
            Raum für Raumkacheln
            <select class={field} value={zone ?? ''} onchange={(e) => (zoneId = Number(e.currentTarget.value))} data-testid="gallery-zone">
              {#each zones as z (z.id)}<option value={z.id}>{z.name}</option>{/each}
            </select>
          </label>
        {/if}
      </div>

      <div class="min-w-0">
        <div class="mb-3 rounded-lg border bg-background/60 p-3" data-testid="gallery-landing">
          <p class="m-0 mb-2 text-sm font-medium">{landing}</p>
          <div class="grid grid-cols-12 gap-1" aria-hidden="true">
            {#each target?.tiles ?? [] as t (t.key)}
              <span class="h-5 rounded bg-muted" style="grid-column: span {t.width}"></span>
            {/each}
            <span class="dash-slot h-5 rounded" style="grid-column: span {slotEntry.defaultWidth}"></span>
          </div>
          <p class="m-0 mt-1 text-xs text-muted-foreground">Gestrichelt: {slotEntry.name}, {WIDTH_LABELS[slotEntry.defaultWidth]}. Die Reihenfolge änderst du danach per Ziehen oder Pfeiltasten.</p>
        </div>

        <p class="sr-only" role="status" aria-live="polite" data-testid="gallery-status">{message ?? ''}</p>
        {#if message}<p class="m-0 mb-2 text-sm font-medium" aria-hidden="true">{message}</p>{/if}

        {#if shown.length === 0}
          <p class="m-0 text-muted-foreground" role="status">Keine Kachel passt zur Suche.</p>
        {:else}
          <ul class="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3">
            {#each shown as entry (entry.kind)}
              {@const blocked = full || (entry.needsZone && zone === null)}
              <li
                class="flex flex-col gap-2 rounded-xl border bg-card p-3"
                data-testid={`gallery-card-${entry.kind}`}
                onmouseenter={() => (hovered = entry.kind)}
                onfocusin={() => (hovered = entry.kind)}
              >
                <TilePreview kind={entry.kind} />
                <div class="min-w-0">
                  <h3 class="m-0 text-sm">{entry.name}</h3>
                  <p class="m-0 text-xs text-muted-foreground">{entry.description}</p>
                </div>
                <Button variant="outline" size="sm" class="mt-auto min-h-9" disabled={blocked} onclick={() => add(entry)} aria-label={`${entry.name} hinzufügen`}>
                  <Plus aria-hidden="true" />Hinzufügen
                </Button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    </div>
    <Dialog.Footer>
      <Dialog.Close>{#snippet child({ props })}<Button {...props}>Fertig</Button>{/snippet}</Dialog.Close>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
