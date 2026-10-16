<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { toFailure, type Failure } from '$lib/rooms/logic';
  import { createZonesClient } from '$lib/zones/client';
  import { createBody, patchOf, valuesOf, validateRoomValues } from '$lib/zones/logic';
  import type { RoomForm, RoomFormOptions, RoomValues } from '$lib/zones/types';

  // Raum anlegen (`zoneId` null) oder bearbeiten. Die Auswahllisten und die aktuellen Werte
  // kommen beim Öffnen vom Server; gespeichert wird genau einmal je Klick, nie automatisch
  // wiederholt. Bei Fehlern bleibt die Eingabe stehen.
  let {
    open = $bindable(false),
    zoneId,
    ondone,
  }: {
    open: boolean;
    zoneId: number | null;
    ondone: (room: RoomForm, created: boolean) => void;
  } = $props();

  const { api } = useApp();
  const client = createZonesClient(api);

  let options = $state<RoomFormOptions | null>(null);
  let loading = $state(false);
  let loadFailure = $state<Failure | null>(null);
  let values = $state<RoomValues>({ name: '', display_name: '', operating_mode: '', sort_order: '0' });
  let busy = $state(false);
  let failure = $state<Failure | null>(null);
  let problem = $state<{ field: string; message: string } | null>(null);
  let generation = 0;

  async function load() {
    const mine = ++generation;
    loading = true;
    loadFailure = null;
    options = null;
    failure = null;
    problem = null;
    try {
      const result = await client.form(zoneId ?? undefined);
      if (mine !== generation) return;
      options = result;
      values = valuesOf(result.room, result.operating_modes[0]?.code ?? '');
    } catch (error) {
      if (mine !== generation) return;
      loadFailure = toFailure(error);
    } finally {
      if (mine === generation) loading = false;
    }
  }

  $effect(() => {
    if (open) void load();
    else generation += 1;
  });

  const creating = $derived(zoneId === null);
  const field = (name: string) => (problem?.field === name ? problem : failure?.field === name ? failure : null);
  const invalid = (name: string) => field(name) !== null;
  const message = (name: string) => problem?.field === name ? problem.message : failure?.field === name ? failure.message : '';
  const inputClass = 'h-10 w-full rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive';

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !options) return;
    failure = null;
    problem = validateRoomValues(values, options);
    if (problem) return;
    let patch = null;
    if (!creating && options.room) {
      patch = patchOf(values, options.room);
      if (Object.keys(patch).length === 0) {
        open = false;
        return;
      }
    }
    busy = true;
    try {
      const room = creating
        ? await client.create(createBody(values))
        : await client.save(zoneId as number, patch ?? {});
      open = false;
      ondone(room, creating);
    } catch (error) {
      failure = toFailure(error);
    } finally {
      busy = false;
    }
  }

  // Fehler ohne Feld (Berechtigung, Sitzung, Netzwerk) stehen über den Tasten.
  const generalFailure = $derived(failure && !['name', 'display_name', 'operating_mode', 'sort_order'].includes(failure.field ?? '') ? failure : null);
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>{creating ? 'Raum anlegen' : 'Raum bearbeiten'}</Dialog.Title>
      <Dialog.Description>
        {creating
          ? 'Der technische Name ist die feste Kennung des Raums; der Anzeigename erscheint in der Oberfläche.'
          : 'Änderungen gelten sofort für den ganzen Raum.'}
      </Dialog.Description>
    </Dialog.Header>

    {#if loading}
      <p class="m-0 text-muted-foreground" role="status">Wird geladen …</p>
    {:else if loadFailure}
      <Notice tone="error">{loadFailure.message}</Notice>
      <Dialog.Footer>
        <Button variant="outline" onclick={() => (open = false)}>Schließen</Button>
        {#if loadFailure.kind !== 'forbidden' && loadFailure.kind !== 'not_found'}
          <Button onclick={load}>Erneut versuchen</Button>
        {/if}
      </Dialog.Footer>
    {:else if options}
      <form class="grid gap-3" onsubmit={submit} novalidate>
        <div>
          <label class="mb-1 block text-sm font-medium" for="rf-name">Technischer Name</label>
          <input id="rf-name" class={inputClass} bind:value={values.name} autocomplete="off" maxlength={options.name_limit + 20} aria-invalid={invalid('name')} aria-describedby={invalid('name') ? 'rf-name-msg' : undefined} />
          {#if invalid('name')}<p id="rf-name-msg" class="mt-1 mb-0 text-sm text-destructive">{message('name')}</p>{/if}
        </div>
        <div>
          <label class="mb-1 block text-sm font-medium" for="rf-display">Anzeigename</label>
          <input id="rf-display" class={inputClass} bind:value={values.display_name} autocomplete="off" aria-invalid={invalid('display_name')} aria-describedby={invalid('display_name') ? 'rf-display-msg' : undefined} />
          {#if invalid('display_name')}<p id="rf-display-msg" class="mt-1 mb-0 text-sm text-destructive">{message('display_name')}</p>{/if}
        </div>
        <div>
          <label class="mb-1 block text-sm font-medium" for="rf-mode">Betriebsart</label>
          <select id="rf-mode" class={inputClass} bind:value={values.operating_mode} aria-invalid={invalid('operating_mode')} aria-describedby={invalid('operating_mode') ? 'rf-mode-msg' : undefined}>
            {#each options.operating_modes as kind (kind.code)}
              <option value={kind.code}>{kind.label}</option>
            {/each}
          </select>
          {#if invalid('operating_mode')}<p id="rf-mode-msg" class="mt-1 mb-0 text-sm text-destructive">{message('operating_mode')}</p>{/if}
        </div>
        <div>
          <label class="mb-1 block text-sm font-medium" for="rf-order">Sortierung</label>
          <input id="rf-order" class={inputClass} inputmode="numeric" bind:value={values.sort_order} autocomplete="off" aria-invalid={invalid('sort_order')} aria-describedby="rf-order-hint{invalid('sort_order') ? ' rf-order-msg' : ''}" />
          <p id="rf-order-hint" class="mt-1 mb-0 text-xs text-muted-foreground">Kleinere Zahlen stehen weiter oben.</p>
          {#if invalid('sort_order')}<p id="rf-order-msg" class="mt-1 mb-0 text-sm text-destructive">{message('sort_order')}</p>{/if}
        </div>
        {#if generalFailure}<Notice tone="error">{generalFailure.message}</Notice>{/if}
        <Dialog.Footer>
          <Button type="button" variant="outline" onclick={() => (open = false)}>Abbrechen</Button>
          <Button type="submit" disabled={busy}>{busy ? 'Wird gespeichert …' : creating ? 'Raum anlegen' : 'Speichern'}</Button>
        </Dialog.Footer>
      </form>
    {/if}
  </Dialog.Content>
</Dialog.Root>
