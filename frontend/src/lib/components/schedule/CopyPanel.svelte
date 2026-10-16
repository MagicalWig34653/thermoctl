<script lang="ts">
  import { Copy } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';
  import { DAY_NAMES, WEEKDAYS } from '$lib/schedule/geometry';
  import type { Intent } from '$lib/schedule/intents';
  import type { Plan } from '$lib/schedule/types';

  let {
    plan,
    activeDay,
    busy,
    onintent,
  }: { plan: Plan; activeDay: number; busy: boolean; onintent: (intent: Intent) => void } = $props();

  let sourceDay = $state(1);
  let picked = $state<number[]>([]);
  let confirmOpen = $state(false);
  $effect(() => {
    sourceDay = activeDay;
  });
  // Nur Räume, in die der Server das Schreiben erlaubt hat (`copy_targets`).
  const targets = $derived(plan.copy_targets);
  const chosen = $derived(targets.filter((t) => picked.includes(t.id)));
  const replacing = $derived(chosen.filter((t) => t.has_plan));

  function toggle(id: number) {
    picked = picked.includes(id) ? picked.filter((p) => p !== id) : [...picked, id];
  }

  function copyWeek() {
    confirmOpen = false;
    onintent({
      type: 'copyWeek',
      targets: chosen.map((t) => ({ zone_id: t.id, version: t.version })),
    });
    picked = [];
  }
</script>

<section aria-labelledby="copy-title" class="rounded-xl border bg-card p-4">
  <h2 id="copy-title" class="mb-1 text-lg">Kopieren</h2>
  <div class="grid gap-6 md:grid-cols-2">
    <div class="grid content-start gap-3">
      <h3 class="m-0 text-base">Tag auf mehrere Tage</h3>
      <p class="m-0 text-sm text-muted-foreground">Der vollständige Tagesplan ersetzt die Pläne der Zieltage.</p>
      <div class="flex flex-wrap items-center gap-2">
        <label class="sr-only" for="copy-source">Quelltag</label>
        <select id="copy-source" class="h-11 rounded-lg border bg-background px-3 text-sm" bind:value={sourceDay}>
          {#each WEEKDAYS as d (d)}<option value={d}>{DAY_NAMES[d]}</option>{/each}
        </select>
        <Button
          variant="outline"
          disabled={busy}
          onclick={() => onintent({ type: 'copyDay', weekday: sourceDay, scope: 'workdays' })}
          >Auf Montag bis Freitag</Button
        >
        <Button
          variant="outline"
          disabled={busy}
          onclick={() => onintent({ type: 'copyDay', weekday: sourceDay, scope: 'all' })}
          >Auf alle Tage</Button
        >
      </div>
    </div>

    <div class="grid content-start gap-3">
      <h3 class="m-0 text-base">Wochenplan in andere Räume</h3>
      {#if targets.length}
        <p class="m-0 text-sm text-muted-foreground">
          Nur Räume, die du bearbeiten darfst. Der Plan dort wird ersetzt.
        </p>
        <fieldset class="m-0 grid gap-1 border-0 p-0">
          <legend class="sr-only">Zielräume</legend>
          {#each targets as target (target.id)}
            <label class="flex min-h-11 items-center gap-3 rounded-md px-2 hover:bg-muted">
              <input
                type="checkbox"
                class="size-5"
                checked={picked.includes(target.id)}
                onchange={() => toggle(target.id)}
              />
              <span>{target.name}{#if target.has_plan}<span class="text-sm text-muted-foreground"> · hat einen Plan</span>{/if}</span>
            </label>
          {/each}
        </fieldset>
        <div>
          <Button variant="outline" disabled={busy || !chosen.length} onclick={() => (confirmOpen = true)}>
            <Copy size={16} aria-hidden="true" />In {chosen.length || ''} {chosen.length === 1 ? 'Raum' : 'Räume'} übernehmen
          </Button>
        </div>
      {:else}
        <p class="m-0 text-sm text-muted-foreground">Es gibt keinen weiteren Raum, in den du schreiben darfst.</p>
      {/if}
    </div>
  </div>
</section>

<Dialog.Root bind:open={confirmOpen}>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>Wochenplan übernehmen?</Dialog.Title>
      <Dialog.Description>
        Der Plan von „{plan.zone_name}“ ersetzt den Plan in {chosen.map((t) => t.name).join(', ')}.
        {#if replacing.length}Vorhandene Pläne gehen dort verloren ({replacing.map((t) => t.name).join(', ')}).{/if}
        Der Vorgang kann hier nicht zurückgenommen werden.
      </Dialog.Description>
    </Dialog.Header>
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (confirmOpen = false)}>Abbrechen</Button>
      <Button onclick={copyWeek}>Übernehmen</Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
