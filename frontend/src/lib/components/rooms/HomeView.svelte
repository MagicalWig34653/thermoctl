<script lang="ts">
  import { onMount } from 'svelte';
  import { Search } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { createResource } from '$lib/rooms/resource.svelte';
  import { FILTERS, filterRooms, formatInstant, type RoomFilter } from '$lib/rooms/logic';
  import type { Rooms } from '$lib/rooms/types';
  import ActuationPanel from './ActuationPanel.svelte';
  import Notice from './Notice.svelte';
  import RoomCard from './RoomCard.svelte';

  const { api } = useApp();
  const rooms = createResource(() => api.get<Rooms>('/ui-api/rooms'));
  onMount(() => rooms.start());

  let query = $state('');
  let filter = $state<RoomFilter>('all');
  const data = $derived(rooms.data);
  const shown = $derived(data ? filterRooms(data.rooms, query, filter) : []);
</script>

<header class="mb-6">
  <h1>Übersicht</h1>
  <p class="text-muted-foreground">Temperaturen, Sollwerte und Heizanforderungen aller Räume.</p>
</header>

{#if rooms.loading && !data}
  <p class="text-muted-foreground" role="status">Räume werden geladen …</p>
{:else if !data}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{rooms.failure?.message ?? 'Die Räume konnten nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => rooms.refresh()}>Erneut versuchen</Button>
  </div>
{:else}
  <div class="space-y-6">
    {#if rooms.failure}
      <Notice tone="warn">
        Aktualisieren fehlgeschlagen: {rooms.failure.message}
        {#if rooms.updatedAt}Angezeigt ist der Stand von {formatInstant(new Date(rooms.updatedAt).toISOString(), data.timezone)}.{/if}
      </Notice>
    {/if}

    <ActuationPanel actuation={data.actuation} />

    <section aria-labelledby="rooms-title">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 id="rooms-title" class="m-0">Räume <span class="text-base font-normal text-muted-foreground">({shown.length}{shown.length !== data.rooms.length ? ` von ${data.rooms.length}` : ''})</span></h2>
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <div class="relative">
            <Search size={16} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <input
              type="search"
              bind:value={query}
              placeholder="Raum suchen"
              aria-label="Räume durchsuchen"
              class="h-10 w-52 max-w-full rounded-lg border bg-background pr-3 pl-9 text-sm"
            />
          </div>
          <div class="flex flex-wrap gap-1" role="group" aria-label="Filter">
            {#each FILTERS as f (f.id)}
              <Button
                variant={filter === f.id ? 'secondary' : 'outline'}
                size="sm"
                class="min-h-9"
                aria-pressed={filter === f.id}
                onclick={() => (filter = f.id)}>{f.label}</Button
              >
            {/each}
          </div>
        </div>
      </div>

      {#if data.rooms.length === 0}
        <p class="text-muted-foreground">Für dein Konto sind keine Räume sichtbar.</p>
      {:else if shown.length === 0}
        <p class="text-muted-foreground" role="status">Kein Raum passt zu Suche und Filter.</p>
      {:else}
        <ul class="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
          {#each shown as room (room.id)}
            <li><RoomCard {room} timezone={data.timezone} nowFraction={data.now_fraction} /></li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
{/if}
