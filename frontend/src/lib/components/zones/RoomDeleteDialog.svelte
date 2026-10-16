<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { toFailure, type Failure } from '$lib/rooms/logic';
  import { createZonesClient } from '$lib/zones/client';
  import { impactLines, impactTotal } from '$lib/zones/logic';
  import type { DeleteImpact } from '$lib/zones/types';

  // Löschen erst nach Anzeige der Folgen und ausdrücklicher Bestätigung. Das Löschen läuft genau
  // einmal; ein unklarer Ausgang (Netzwerk) wird gemeldet und nie von selbst wiederholt.
  let {
    open = $bindable(false),
    zoneId,
    ondone,
  }: {
    open: boolean;
    zoneId: number;
    ondone: (impact: DeleteImpact) => void;
  } = $props();

  const { api } = useApp();
  const client = createZonesClient(api);

  let impact = $state<DeleteImpact | null>(null);
  let loading = $state(false);
  let loadFailure = $state<Failure | null>(null);
  let confirmed = $state(false);
  let busy = $state(false);
  let failure = $state<Failure | null>(null);
  let generation = 0;

  async function load() {
    const mine = ++generation;
    loading = true;
    loadFailure = null;
    impact = null;
    confirmed = false;
    failure = null;
    try {
      const result = await client.deleteImpact(zoneId);
      if (mine === generation) impact = result;
    } catch (error) {
      if (mine === generation) loadFailure = toFailure(error);
    } finally {
      if (mine === generation) loading = false;
    }
  }

  $effect(() => {
    if (open) void load();
    else generation += 1;
  });

  const lines = $derived(impact ? impactLines(impact) : []);
  const total = $derived(impact ? impactTotal(impact) : 0);

  async function remove() {
    if (busy || !impact || !confirmed) return;
    busy = true;
    failure = null;
    const done = impact;
    try {
      await client.remove(zoneId);
      open = false;
      ondone(done);
    } catch (error) {
      failure = toFailure(error);
    } finally {
      busy = false;
    }
  }
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>Raum löschen</Dialog.Title>
      <Dialog.Description>Das Löschen kann nicht rückgängig gemacht werden.</Dialog.Description>
    </Dialog.Header>

    {#if loading}
      <p class="m-0 text-muted-foreground" role="status">Folgen werden geprüft …</p>
    {:else if loadFailure}
      <Notice tone="error">{loadFailure.message}</Notice>
      <Dialog.Footer>
        <Button variant="outline" onclick={() => (open = false)}>Schließen</Button>
        {#if loadFailure.kind !== 'forbidden' && loadFailure.kind !== 'not_found'}
          <Button onclick={load}>Erneut versuchen</Button>
        {/if}
      </Dialog.Footer>
    {:else if impact}
      <div class="grid gap-3">
        <p class="m-0">Raum <strong>{impact.display_name}</strong> <span class="text-muted-foreground">({impact.name})</span> wird gelöscht.</p>
        {#if lines.length > 0}
          <div>
            <p class="mb-1 text-sm font-medium">Mit dem Raum verschwinden:</p>
            <ul class="m-0 list-disc pl-5 text-sm" data-testid="impact-list">
              {#each lines as line (line.label)}
                <li>{line.count} {line.label}</li>
              {/each}
            </ul>
          </div>
        {:else}
          <p class="m-0 text-sm text-muted-foreground">An diesem Raum hängen keine Pläne, Zuordnungen oder Aufzeichnungen.</p>
        {/if}
        <p class="m-0 text-sm text-muted-foreground">Kacheln dieses Raums in persönlichen Dashboards entfallen ebenfalls. Geräte und andere Räume bleiben bestehen.</p>
        <label class="flex min-h-10 items-start gap-2 text-sm">
          <input type="checkbox" class="mt-1 size-4" bind:checked={confirmed} />
          <span>Ich habe verstanden{total > 0 ? `, dass diese ${total} Einträge verloren gehen` : ''}, und möchte den Raum endgültig löschen.</span>
        </label>
        {#if failure}<Notice tone="error">{failure.message}</Notice>{/if}
        <Dialog.Footer>
          <Button variant="outline" onclick={() => (open = false)}>Abbrechen</Button>
          <Button variant="destructive" disabled={!confirmed || busy} onclick={remove}>{busy ? 'Wird gelöscht …' : 'Raum löschen'}</Button>
        </Dialog.Footer>
      </div>
    {/if}
  </Dialog.Content>
</Dialog.Root>
