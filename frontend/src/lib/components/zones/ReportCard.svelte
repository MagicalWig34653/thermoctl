<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { toFailure, type Failure } from '$lib/rooms/logic';
  import { createZonesClient } from '$lib/zones/client';
  import { noteRemaining } from '$lib/zones/logic';
  import type { ReportOptions } from '$lib/zones/types';

  // Problem melden. Mit `zoneId` für genau diesen Raum (Raumdetail; die Karte bleibt weg, wenn
  // der Server den Raum nicht zum Melden anbietet), ohne `zoneId` mit Raumauswahl (Konto).
  // Das Senden läuft genau einmal je Klick und wird nie automatisch wiederholt.
  let { zoneId = null, hideWhenEmpty = false }: { zoneId?: number | null; hideWhenEmpty?: boolean } = $props();

  const { api } = useApp();
  const client = createZonesClient(api);

  let options = $state<ReportOptions | null>(null);
  let loadFailure = $state<Failure | null>(null);
  let loading = $state(true);
  let chosenRoom = $state<string>('');
  let kind = $state('');
  let note = $state('');
  let busy = $state(false);
  let failure = $state<Failure | null>(null);
  let sent = $state<string | null>(null);
  let attempted = $state(false);

  async function load() {
    loading = true;
    loadFailure = null;
    try {
      options = await client.reportOptions();
    } catch (error) {
      loadFailure = toFailure(error);
    } finally {
      loading = false;
    }
  }
  onMount(() => void load());

  const rooms = $derived(
    options ? (zoneId === null ? options.rooms : options.rooms.filter((room) => room.id === zoneId)) : [],
  );
  const target = $derived(zoneId ?? (chosenRoom ? Number(chosenRoom) : rooms.length === 1 ? rooms[0].id : null));
  const noKind = $derived(attempted && !kind);
  const noRoom = $derived(attempted && target === null);
  const idSuffix = $derived(zoneId ?? 'konto');

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !options) return;
    attempted = true;
    sent = null;
    failure = null;
    if (!kind || target === null) return;
    busy = true;
    try {
      const result = await client.report(target, { kind, note });
      sent = result.message;
      note = '';
      kind = '';
      attempted = false;
    } catch (error) {
      failure = toFailure(error);
    } finally {
      busy = false;
    }
  }
  const inputClass = 'h-10 w-full rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive';
</script>

{#if loading}
  {#if !hideWhenEmpty}<p class="text-sm text-muted-foreground" role="status">Meldeoptionen werden geladen …</p>{/if}
{:else if loadFailure}
  {#if !hideWhenEmpty || loadFailure.kind === 'network' || loadFailure.kind === 'server'}
    <section class="grid max-w-xl gap-3 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="report-title-{idSuffix}">
      <h2 id="report-title-{idSuffix}" class="m-0 text-lg">Problem melden</h2>
      <Notice tone="error">{loadFailure.message}</Notice>
      <div><Button variant="outline" onclick={load}>Erneut versuchen</Button></div>
    </section>
  {/if}
{:else if options && rooms.length === 0}
  {#if !hideWhenEmpty}
    <section class="grid max-w-xl gap-3 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="report-title-{idSuffix}">
      <h2 id="report-title-{idSuffix}" class="m-0 text-lg">Problem melden</h2>
      <p class="m-0 text-sm text-muted-foreground">Für dein Konto ist kein Raum zum Melden freigegeben.</p>
    </section>
  {/if}
{:else if options}
  <section class="grid max-w-xl gap-3 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="report-title-{idSuffix}" data-testid="report-card">
    <div>
      <h2 id="report-title-{idSuffix}" class="m-0 text-lg">Problem melden</h2>
      <p class="m-0 text-sm text-muted-foreground">Die Meldung geht an die Betreiber der Anlage und wird protokolliert.</p>
    </div>
    <form class="grid gap-3" onsubmit={submit} novalidate>
      {#if zoneId === null && rooms.length > 1}
        <div>
          <label class="mb-1 block text-sm font-medium" for="rp-room-{idSuffix}">Raum</label>
          <select id="rp-room-{idSuffix}" class={inputClass} bind:value={chosenRoom} aria-invalid={noRoom}>
            <option value="">Bitte wählen</option>
            {#each rooms as room (room.id)}<option value={String(room.id)}>{room.name}</option>{/each}
          </select>
          {#if noRoom}<p class="mt-1 mb-0 text-sm text-destructive">Bitte einen Raum auswählen.</p>{/if}
        </div>
      {:else if zoneId === null}
        <p class="m-0 text-sm">Raum: <strong>{rooms[0].name}</strong></p>
      {/if}
      <fieldset class="m-0 grid gap-1 border-0 p-0" aria-describedby={noKind ? `rp-kind-msg-${idSuffix}` : undefined}>
        <legend class="mb-1 text-sm font-medium">Was ist das Problem?</legend>
        {#each options.kinds as option (option.code)}
          <label class="flex min-h-10 items-center gap-2 text-sm">
            <input type="radio" name="rp-kind-{idSuffix}" value={option.code} bind:group={kind} />
            {option.label}
          </label>
        {/each}
        {#if noKind}<p id="rp-kind-msg-{idSuffix}" class="m-0 text-sm text-destructive">Bitte eine Problemart auswählen.</p>{/if}
      </fieldset>
      <div>
        <label class="mb-1 block text-sm font-medium" for="rp-note-{idSuffix}">Hinweis (freiwillig)</label>
        <textarea
          id="rp-note-{idSuffix}"
          class="min-h-20 w-full rounded-lg border bg-background px-3 py-2 text-sm"
          bind:value={note}
          rows="3"
          aria-describedby="rp-note-hint-{idSuffix}"
        ></textarea>
        <p id="rp-note-hint-{idSuffix}" class="mt-1 mb-0 text-xs text-muted-foreground">
          Mitgesendet werden höchstens {options.note_limit} Zeichen, noch {noteRemaining(note, options.note_limit)} frei. Der Rest wird abgeschnitten.
        </p>
      </div>
      {#if failure}<Notice tone="error">{failure.message}</Notice>{/if}
      {#if sent}<Notice tone="success">{sent}</Notice>{/if}
      <div><Button type="submit" disabled={busy}>{busy ? 'Wird gesendet …' : 'Problem melden'}</Button></div>
    </form>
  </section>
{/if}
