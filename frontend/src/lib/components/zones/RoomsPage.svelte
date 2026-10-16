<script lang="ts">
  import { onMount } from 'svelte';
  import { Pencil, Plus, Trash2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { appHref } from '$lib/paths';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { createResource } from '$lib/rooms/resource.svelte';
  import type { Rooms } from '$lib/rooms/types';
  import RoomDeleteDialog from './RoomDeleteDialog.svelte';
  import RoomFormDialog from './RoomFormDialog.svelte';

  // Verwaltung der Räume: anlegen, bearbeiten, löschen. Welche Zeile bearbeitbar ist, sagt der
  // Server je Raum (`actions.set_operating_mode` = zone.manage in der Anlagensicht); der Server
  // prüft jeden Aufruf trotzdem erneut.
  const { api, prefix } = useApp();
  const rooms = createResource(() => api.get<Rooms>('/ui-api/rooms'));
  onMount(() => rooms.start());

  let formOpen = $state(false);
  let formZone = $state<number | null>(null);
  let deleteOpen = $state(false);
  let deleteZone = $state(0);
  let notice = $state<string | null>(null);

  const list = $derived(rooms.data?.rooms ?? []);

  function openCreate() {
    notice = null;
    formZone = null;
    formOpen = true;
  }
  function openEdit(id: number) {
    notice = null;
    formZone = id;
    formOpen = true;
  }
  function openDelete(id: number) {
    notice = null;
    deleteZone = id;
    deleteOpen = true;
  }
</script>

<header class="mb-6 flex flex-wrap items-start justify-between gap-3">
  <div class="min-w-0">
    <h1>Räume</h1>
    <p class="text-muted-foreground">Räume anlegen, umbenennen, sortieren und löschen.</p>
  </div>
  <Button onclick={openCreate} data-testid="room-create"><Plus aria-hidden="true" />Raum anlegen</Button>
</header>

{#if notice}<div class="mb-4"><Notice tone="success">{notice}</Notice></div>{/if}

{#if rooms.loading && !rooms.data}
  <p class="text-muted-foreground" role="status">Räume werden geladen …</p>
{:else if !rooms.data}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{rooms.failure?.message ?? 'Die Räume konnten nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => rooms.refresh()}>Erneut versuchen</Button>
  </div>
{:else}
  {#if rooms.failure}
    <div class="mb-4"><Notice tone="warn">Aktualisieren fehlgeschlagen: {rooms.failure.message} Angezeigt ist der letzte bekannte Stand.</Notice></div>
  {/if}
  {#if list.length === 0}
    <p class="text-muted-foreground" data-testid="rooms-empty">Es gibt noch keine Räume, die du sehen darfst.</p>
  {:else}
    <ul class="m-0 grid max-w-3xl list-none gap-2 p-0" data-testid="room-list">
      {#each list as room (room.id)}
        <li class="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border bg-card p-3 sm:p-4" data-testid="room-row">
          <div class="min-w-0 flex-1 basis-48">
            <a class="text-base font-medium" href={appHref(prefix, `/raeume/${room.id}`)}>{room.name}</a>
            <div class="text-sm text-muted-foreground">
              {room.operating_mode.label} · Ist {formatCelsius(room.temperature_c)} · Soll {formatCelsius(room.setpoint.temperature_c)}
            </div>
          </div>
          {#if room.actions.set_operating_mode}
            <div class="flex gap-2">
              <Button variant="outline" onclick={() => openEdit(room.id)} aria-label={`Raum ${room.name} bearbeiten`}><Pencil aria-hidden="true" />Bearbeiten</Button>
              <Button variant="outline" onclick={() => openDelete(room.id)} aria-label={`Raum ${room.name} löschen`}><Trash2 aria-hidden="true" />Löschen</Button>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
{/if}

<RoomFormDialog
  bind:open={formOpen}
  zoneId={formZone}
  ondone={(room, created) => {
    notice = created ? `Raum „${room.display_name}“ angelegt.` : `Raum „${room.display_name}“ gespeichert.`;
    void rooms.refresh();
  }}
/>
<RoomDeleteDialog
  bind:open={deleteOpen}
  zoneId={deleteZone}
  ondone={(impact) => {
    notice = `Raum „${impact.display_name}“ gelöscht.`;
    void rooms.refresh();
  }}
/>
