<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { scheduleFailure } from '$lib/schedule/errors';
  import { createScheduleClient } from '$lib/schedule/client';
  import type { ModeAdmin } from '$lib/schedule/types';

  // Temperaturmodi verwalten (Anlagensicht, Recht `mode.manage`): Namen und Reihenfolge.
  // Temperaturen je Raum stehen bei der Raumtechnik; ob ein Modus gelöscht werden darf, sagt der Server (`delete_lock`).
  const { api } = useApp();
  const client = createScheduleClient(api);

  let modes = $state<ModeAdmin[] | null>(null);
  let loadError = $state<string | null>(null);
  let message = $state<{ tone: 'error' | 'success'; text: string } | null>(null);
  let busy = $state(false);
  let confirming = $state<number | null>(null);
  let drafts = $state<Record<number, { code: string; name: string; sort_order: string }>>({});
  let fresh = $state({ code: '', name: '', sort_order: '0' });

  function take(next: { modes: ModeAdmin[] }) {
    modes = next.modes;
    drafts = Object.fromEntries(
      next.modes.map((m) => [m.id, { code: m.code, name: m.name, sort_order: String(m.sort_order) }]),
    );
  }
  async function load() {
    try {
      take(await client.modes());
      loadError = null;
    } catch (error) {
      loadError = scheduleFailure(error).message;
    }
  }
  onMount(() => void load());

  async function run(action: () => Promise<{ modes: ModeAdmin[] }>, ok: string) {
    if (busy) return;
    busy = true;
    message = null;
    try {
      take(await action());
      message = { tone: 'success', text: ok };
    } catch (error) {
      message = { tone: 'error', text: scheduleFailure(error).message };
    } finally {
      busy = false;
      confirming = null;
    }
  }

  const sortOf = (text: string) => (Number.isInteger(Number(text)) && text.trim() !== '' ? Number(text) : 0);
</script>

<section class="max-w-[820px] rounded-xl border bg-card p-4" aria-labelledby="modes-title" data-testid="modes">
  <h2 id="modes-title">Temperaturmodi</h2>
  <p class="text-sm text-muted-foreground">Die Modi, aus denen Wochenpläne bestehen, zum Beispiel Tag und Nacht.</p>
  {#if loadError && !modes}
    <Notice tone="error">{loadError}</Notice>
  {:else if !modes}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if message}<div class="mb-3"><Notice tone={message.tone}>{message.text}</Notice></div>{/if}
    <ul class="m-0 grid list-none gap-3 p-0">
      {#each modes as mode (mode.id)}
        {@const d = drafts[mode.id]}
        <li class="grid gap-2 rounded-lg border p-3">
          <div class="grid gap-2 sm:grid-cols-[1fr_1fr_6rem]">
            <label class="grid gap-1 text-sm"><span>Name</span><input class="h-11 rounded-lg border bg-background px-3" bind:value={d.name} /></label>
            <label class="grid gap-1 text-sm"><span>Kürzel</span><input class="h-11 rounded-lg border bg-background px-3" bind:value={d.code} /></label>
            <label class="grid gap-1 text-sm"><span>Reihenfolge</span><input inputmode="numeric" class="h-11 rounded-lg border bg-background px-3" bind:value={d.sort_order} /></label>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Button variant="outline" disabled={busy} onclick={() => run(() => client.renameMode(mode.id, { code: d.code, name: d.name, sort_order: sortOf(d.sort_order) }), `„${d.name}“ gespeichert.`)}>Speichern</Button>
            {#if mode.delete_lock}
              <span class="text-sm text-muted-foreground">Nicht löschbar: {mode.delete_lock}</span>
            {:else if confirming === mode.id}
              <p class="m-0 basis-full text-sm" data-testid="mode-delete-hint">Soll der Modus „{mode.name}“ wirklich gelöscht werden? Vorhandene Sollwerte dieses Modus werden ebenfalls gelöscht.</p>
              <Button variant="destructive" disabled={busy} onclick={() => run(() => client.removeMode(mode.id), `„${mode.name}“ gelöscht.`)}>Wirklich löschen</Button>
              <Button variant="ghost" onclick={() => (confirming = null)}>Abbrechen</Button>
            {:else}
              <Button variant="ghost" disabled={busy} onclick={() => (confirming = mode.id)}>Löschen</Button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
    <form
      class="mt-4 grid gap-2 rounded-lg border p-3"
      onsubmit={(e) => {
        e.preventDefault();
        void run(() => client.addMode({ code: fresh.code, name: fresh.name, sort_order: sortOf(fresh.sort_order) }), `„${fresh.name}“ angelegt.`).then(() => {
          if (message?.tone === 'success') fresh = { code: '', name: '', sort_order: '0' };
        });
      }}
    >
      <h3 class="m-0 text-base">Neuer Modus</h3>
      <div class="grid gap-2 sm:grid-cols-[1fr_1fr_6rem]">
        <label class="grid gap-1 text-sm"><span>Name</span><input required class="h-11 rounded-lg border bg-background px-3" bind:value={fresh.name} /></label>
        <label class="grid gap-1 text-sm"><span>Kürzel</span><input required class="h-11 rounded-lg border bg-background px-3" bind:value={fresh.code} /></label>
        <label class="grid gap-1 text-sm"><span>Reihenfolge</span><input inputmode="numeric" class="h-11 rounded-lg border bg-background px-3" bind:value={fresh.sort_order} /></label>
      </div>
      <div><Button type="submit" disabled={busy}>Modus anlegen</Button></div>
    </form>
  {/if}
</section>
