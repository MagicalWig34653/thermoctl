<script lang="ts">
  import { onMount } from 'svelte';
  import { Redo2, Undo2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createScheduleClient } from '$lib/schedule/client';
  import { createEditor } from '$lib/schedule/editor.svelte';
  import { zoneLabel } from '$lib/schedule/format';
  import { modeColor, refOf } from '$lib/schedule/geometry';
  import type { Intent } from '$lib/schedule/intents';
  import { formatCelsius } from '$lib/rooms/decimal';
  import CopyPanel from './CopyPanel.svelte';
  import Inspector from './Inspector.svelte';
  import PreviewStrip from './PreviewStrip.svelte';
  import WeekGrid from './WeekGrid.svelte';

  let { zoneId }: { zoneId: number } = $props();
  const { api } = useApp();
  // Pro Raum eine neue Instanz (der Aufrufer setzt `{#key zoneId}`).
  // svelte-ignore state_referenced_locally
  const editor = createEditor(createScheduleClient(api), zoneId);
  onMount(() => void editor.load());

  let brush = $state<number | null>(null);
  let activeDay = $state(1);
  let inspector = $state<{ focus: () => void } | null>(null);

  const plan = $derived(editor.plan);
  // Der Server kennt keinen „Grundmodus“: jeder Modus ist wählbar, vorbelegt ist der wärmste.
  const brushModes = $derived(plan ? plan.modes : []);
  const brushMode = $derived(
    brushModes.find((m) => m.id === brush) ??
      brushModes.reduce<(typeof brushModes)[number] | null>((best, m) => (!best || m.warmth > best.warmth ? m : best), null),
  );
  const selected = $derived(editor.selectedBlock);

  const act = (intent: Intent) => void editor.submit(intent);
  const say = (text: string) => (editor.notice = { tone: 'warn', text });
  function remove() {
    const block = editor.selectedBlock;
    if (!block) return;
    act({ type: 'delete', block: refOf(block) });
  }
  function history(kind: 'undo' | 'redo') {
    void (kind === 'undo' ? editor.undo() : editor.redo());
  }
  // Schmale Ansicht: Der Inspektor liegt als Sheet unten. Der gewählte Block wird in den
  // freien oberen Teil gerollt, damit man sieht, was man bearbeitet.
  $effect(() => {
    const ref = editor.selected;
    if (!ref || typeof window === 'undefined' || window.matchMedia('(min-width: 1024px)').matches) return;
    queueMicrotask(() => {
      const el = document.querySelector<HTMLElement>(`[data-block="${ref.weekday}-${ref.start}-${ref.end}"]`);
      if (!el) return;
      const rect = el.getBoundingClientRect();
      if (rect.top < 70 || rect.bottom > window.innerHeight * 0.36) {
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        window.scrollBy({ top: rect.top - 120, behavior: reduce ? 'auto' : 'smooth' });
      }
    });
  });

  async function openInspector() {
    await Promise.resolve();
    inspector?.focus();
  }
</script>

{#if editor.loading && !plan}
  <p class="text-muted-foreground" role="status">Wochenplan wird geladen …</p>
{:else if !plan}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{editor.loadError ?? 'Der Wochenplan konnte nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => editor.reload()}>Erneut versuchen</Button>
  </div>
{:else}
  <div class="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_23rem]">
    <section class="min-w-0 rounded-xl border bg-card p-4" aria-labelledby="plan-title" data-testid="plan-card">
      <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="plan-title" class="mb-1">{plan.zone_name}</h2>
          <p class="m-0 text-sm text-muted-foreground">
            {#if !plan.can_edit}Du kannst diesen Plan ansehen.
            {:else if !plan.full_editor}In dieser Ansicht kannst du Pläne kopieren. Einzelne Zeiten ändert die Verwaltung.
            {:else}Auf dem Plan ziehen, um eine Zeit anzulegen.{/if}
          </p>
        </div>
        {#if plan.can_edit}
          <div class="flex items-center gap-1" role="group" aria-label="Verlauf der Änderungen">
            <Button variant="ghost" size="icon" aria-label="Rückgängig" disabled={!editor.canUndo} onclick={() => history('undo')}>
              <Undo2 size={18} aria-hidden="true" />
            </Button>
            <Button variant="ghost" size="icon" aria-label="Wiederholen" disabled={!editor.canRedo} onclick={() => history('redo')}>
              <Redo2 size={18} aria-hidden="true" />
            </Button>
          </div>
        {/if}
      </div>

      {#if editor.fullEditor && brushMode}
        <div class="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Neue Zeit mit">
          <span class="text-sm text-muted-foreground">Neue Zeit mit</span>
          {#each brushModes as mode (mode.id)}
            <button
              type="button"
              class="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm hover:bg-muted aria-pressed:border-foreground aria-pressed:bg-muted"
              aria-pressed={brushMode.id === mode.id}
              onclick={() => (brush = mode.id)}
            >
              <span class="size-3 rounded-full" style="background:{modeColor(mode.warmth)}" aria-hidden="true"></span>
              {mode.name}
              {#if mode.temperature_c}<span class="text-muted-foreground">{formatCelsius(mode.temperature_c)}</span>{/if}
            </button>
          {/each}
        </div>
      {/if}

      {#if editor.conflict}
        <div class="mb-3 flex flex-wrap items-center gap-3">
          <Notice tone="error">Plan wurde in einer anderen Sitzung geändert. Bearbeiten ist gesperrt, bis du neu lädst.</Notice>
          <Button onclick={() => editor.reload()} data-testid="reload">Neu laden</Button>
        </div>
      {:else if editor.notice}
        <div class="mb-3"><Notice tone={editor.notice.tone}>{editor.notice.text}</Notice></div>
      {/if}

      <WeekGrid
        blocks={editor.blocks}
        selected={editor.selected}
        ghost={editor.ghost}
        editable={editor.fullEditor && !editor.busy}
        brushModeId={brushMode?.id ?? null}
        bind:activeDay
        onselect={(ref) => editor.select(ref)}
        onopen={openInspector}
        onintent={act}
        onmessage={say}
        onremove={remove}
        onhistory={history}
      />

      <div class="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-sm text-muted-foreground">
        <span>Der Plan ist ein Ring: Jeder Zeitbereich gilt bis zum nächsten.</span>
        <span role="status" aria-live="polite" data-testid="status">{editor.busy ? 'Wird gespeichert …' : editor.status}</span>
      </div>
      <p class="mt-2 mb-0 text-xs text-muted-foreground" data-testid="tz-hint">
        Zeiten gelten in der Ortszeit der Anlage ({zoneLabel(plan.timezone)}). Bei der Umstellung auf Sommer- oder Winterzeit
        ordnet der Server die Schaltzeiten zu.
      </p>
      {#if editor.fullEditor}
        <details class="mt-3 text-sm">
          <summary class="cursor-pointer rounded-md py-1">Bedienung</summary>
          <ul class="m-0 mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
            <li>Ziehen auf einem langen Zeitbereich oder leerem Plan legt eine neue Zeit an und teilt den Bereich; sie darf keinen anderen Beginn überdecken. Umschalt + Ziehen übermalt auch diese.</li>
            <li>Einen kurzen Zeitbereich ziehen verschiebt ihn, mit Option/Alt wird er dupliziert. Was er verlässt, übernimmt der Modus davor. Esc bricht die Geste ab.</li>
            <li>Die Ränder links und rechts ändern Beginn und Ende; die gemeinsame Kante zum Nachbarn wandert mit (Alt beim Verkleinern: nicht).</li>
            <li>Mit Tab zu einem Block: Pfeile verschieben, Alt + Pfeile ändern Ende (←/→) und Beginn (↑/↓), Enter öffnet die Details.</li>
            <li>Rücktaste oder Entf löscht den markierten Block, Strg/Cmd + Z nimmt zurück, mit Umschalt wird wiederholt.</li>
          </ul>
        </details>
      {/if}
    </section>

    <aside
      aria-label="Zeit anpassen"
      class="{selected
        ? 'fixed inset-x-0 bottom-0 z-40 max-h-[58vh] overflow-y-auto rounded-t-2xl border-t bg-card p-4 shadow-2xl'
        : 'hidden'} lg:static lg:z-auto lg:block lg:max-h-none lg:self-start lg:rounded-xl lg:border lg:shadow-none"
      data-testid="inspector-panel"
    >
      {#if selected}
        <Inspector
          bind:this={inspector}
          block={selected}
          blocks={editor.blocks}
          modes={plan.modes}
          editable={editor.fullEditor && !editor.busy}
          onintent={act}
          onmessage={say}
          onclose={() => editor.select(null)}
        />
      {:else}
        <div class="hidden gap-2 p-4 lg:grid">
          <p class="m-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">Wochenplan</p>
          <h3 class="m-0 text-xl">Zeiten anpassen</h3>
          <p class="m-0 text-sm text-muted-foreground">
            Wähle eine Zeit in der Übersicht. Hier erscheinen dann Temperatur und exakte Zeiten.
          </p>
        </div>
      {/if}
    </aside>
  </div>
  {#if selected}<div class="h-[58vh] lg:hidden" aria-hidden="true"></div>{/if}

  <div class="mt-6 grid gap-6">
    <PreviewStrip preview={editor.preview} error={editor.previewError} />
    {#if plan.can_edit}
      <CopyPanel {plan} {activeDay} busy={editor.busy || editor.conflict} onintent={act} />
    {/if}
  </div>
{/if}
