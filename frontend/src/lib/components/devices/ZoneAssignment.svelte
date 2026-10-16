<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { deviceFailure, failureText } from '$lib/devices/errors';
  import type { ZoneDevices } from '$lib/devices/types';

  let { zoneId }: { zoneId: number } = $props();
  const { api } = useApp();
  const client = createDevicesClient(api);

  let data = $state<ZoneDevices | null>(null);
  let loadError = $state<string | null>(null);
  let busy = $state(false);
  let notice = $state<{ tone: 'error' | 'success' | 'warn'; text: string } | null>(null);
  let generation = 0;
  let sourceDevice = $state('');

  async function load() {
    const mine = ++generation;
    try {
      const value = await client.zoneDevices(zoneId);
      if (mine !== generation) return;
      data = value;
      sourceDevice = value.temperature_source ? String(value.temperature_source.id) : '';
      loadError = null;
    } catch (error) {
      if (mine !== generation) return;
      loadError = deviceFailure(error).message;
    }
  }
  onMount(() => {
    void load();
    return () => (generation += 1);
  });

  // Ein Schreibaufruf läuft einmal; nach einem Fehler wird nur gelesen, nie erneut geschrieben.
  async function run(action: () => Promise<ZoneDevices>, ok: string): Promise<boolean> {
    if (busy) return false;
    busy = true;
    notice = null;
    try {
      generation += 1;
      data = await action();
      sourceDevice = data.temperature_source ? String(data.temperature_source.id) : '';
      notice = { tone: 'success', text: ok };
      return true;
    } catch (error) {
      const f = deviceFailure(error);
      notice = { tone: f.isConflict ? 'warn' : 'error', text: failureText(f) };
      if (f.reload) await load();
      return false;
    } finally {
      busy = false;
    }
  }

  // -- Formulare ---------------------------------------------------------------------------
  let addDevice = $state('');
  let addRole = $state('');
  let swapOld = $state('');
  let swapNew = $state('');
  let confirmSwap = $state(false);
  let confirmDetach = $state<number | null>(null);
  let modeDraft = $state<Record<number, string>>({});

  const grouped = $derived.by(() => {
    const out = new Map<string, NonNullable<typeof data>['assignments']>();
    for (const a of data?.assignments ?? []) out.set(a.role.label, [...(out.get(a.role.label) ?? []), a]);
    return [...out];
  });
  const assignedDevices = $derived.by(() => {
    const seen = new Map<number, string>();
    for (const a of data?.assignments ?? []) seen.set(a.device.id, a.device.name);
    return [...seen];
  });
  const modeInfo = (code: string) => data?.thermostat_modes.find((m) => m.code === code);
  const needs = (role: string) => data?.required_capabilities[role]?.join(', ') ?? '';

  async function add(e: SubmitEvent) {
    e.preventDefault();
    if (await run(() => client.assign(zoneId, Number(addDevice), addRole), 'Gerät zugeordnet.')) {
      addDevice = '';
    }
  }
  async function setSource(e: SubmitEvent) {
    e.preventDefault();
    await run(() => client.source(zoneId, sourceDevice ? Number(sourceDevice) : null), sourceDevice ? 'Messquelle gesetzt.' : 'Messquelle gelöst.');
  }
  async function detach(id: number) {
    if (confirmDetach !== id) {
      confirmDetach = id;
      return;
    }
    confirmDetach = null;
    await run(() => client.detach(zoneId, id), 'Zuordnung gelöst. Ein Aktor behält seinen letzten Zustand.');
  }
  async function swap(e: SubmitEvent) {
    e.preventDefault();
    if (!confirmSwap) {
      confirmSwap = true;
      return;
    }
    confirmSwap = false;
    if (await run(() => client.swap(zoneId, Number(swapOld), Number(swapNew)), 'Gerät ausgetauscht. Alle Rollen wurden übernommen.')) {
      swapOld = '';
      swapNew = '';
    }
  }
  async function applyMode(id: number) {
    const mode = modeDraft[id];
    if (!mode) return;
    if (await run(() => client.regulation(zoneId, id, mode), 'Betriebsart gesetzt.')) delete modeDraft[id];
  }
</script>

<section aria-labelledby="zone-title" class="grid gap-4" data-testid="zone-assignment">
  {#if loadError && !data}
    <Notice tone="error">{loadError}</Notice>
  {:else if !data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    <div>
      <h2 id="zone-title" class="m-0">{data.zone.name}</h2>
      {#if !data.may_edit}<p class="m-0 text-sm text-muted-foreground">Nur Ansicht: Für das Ändern fehlt die Berechtigung.</p>{/if}
    </div>
    {#if notice}<Notice tone={notice.tone}>{notice.text}</Notice>{/if}
    {#if loadError}<Notice tone="error">{loadError}</Notice>{/if}
    {#if data.picture.defects.length}
      <Notice tone="warn">
        <ul class="m-0 list-none p-0">{#each data.picture.defects as d, i (i)}<li>{d}</li>{/each}</ul>
      </Notice>
    {/if}

    <div class="rounded-xl border bg-card p-4">
      <h3 class="m-0 text-base">Temperaturquelle</h3>
      <p class="m-0 text-sm text-muted-foreground">Der Fühler, dessen Wert die Regelung des Raums verwendet.</p>
      <p class="mt-2" data-testid="source">{data.temperature_source ? data.temperature_source.name : 'Keine Messquelle gesetzt.'}</p>
      {#if data.may_edit}
        <form class="flex flex-wrap items-end gap-2" onsubmit={setSource}>
          <label class="grid gap-1 text-sm"><span>Gerät</span>
            <select class="h-11 min-w-48 rounded-lg border bg-background px-3" bind:value={sourceDevice}>
              <option value="">Keine Messquelle</option>
              {#each data.devices as d (d.id)}<option value={String(d.id)}>{d.name}</option>{/each}
            </select></label>
          <Button type="submit" variant="outline" disabled={busy}>Messquelle übernehmen</Button>
        </form>
      {/if}
    </div>

    <div class="rounded-xl border bg-card p-4">
      <h3 class="m-0 text-base">Zugeordnete Geräte</h3>
      <p class="m-0 text-sm text-muted-foreground">Mehrere Fühler und Fensterkontakte je Raum sind möglich; ein Aktor gehört nur zu einem Raum.</p>
      {#if !grouped.length}
        <p class="mt-2 text-muted-foreground">Diesem Raum ist noch kein Gerät zugeordnet.</p>
      {/if}
      {#each grouped as [role, rows] (role)}
        <h4 class="mb-1 mt-3 text-sm font-semibold">{role}{rows.length > 1 ? ` (${rows.length})` : ''}</h4>
        <ul class="m-0 grid list-none gap-2 p-0">
          {#each rows as a (a.id)}
            <li class="grid gap-2 rounded-lg border p-3" data-testid="assignment">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="min-w-0 break-words font-medium">{a.device.name}</span>
                {#if data.may_edit}
                  <Button variant={confirmDetach === a.id ? 'destructive' : 'outline'} size="sm" disabled={busy} onclick={() => detach(a.id)}>
                    {confirmDetach === a.id ? 'Wirklich lösen' : 'Lösen'}
                  </Button>
                {/if}
              </div>
              {#if a.is_thermostat}
                <div class="grid gap-2">
                  <div class="text-sm">Betriebsart: <strong data-testid="mode-label">{a.thermostat_mode_label ?? a.thermostat_mode ?? 'unbekannt'}</strong></div>
                  {#if data.may_edit}
                    {@const draft = modeDraft[a.id] ?? a.thermostat_mode ?? ''}
                    <div class="flex flex-wrap items-end gap-2">
                      <label class="grid gap-1 text-sm"><span>Betriebsart ändern</span>
                        <select class="h-11 rounded-lg border bg-background px-3" value={draft} onchange={(e) => (modeDraft[a.id] = e.currentTarget.value)}>
                          {#each data.thermostat_modes as m (m.code)}<option value={m.code}>{m.label}</option>{/each}
                        </select></label>
                      <Button variant="outline" disabled={busy || draft === a.thermostat_mode} onclick={() => applyMode(a.id)}>Übernehmen</Button>
                    </div>
                    {#if modeInfo(draft)}
                      <p class="m-0 text-sm text-muted-foreground">{modeInfo(draft)?.description}</p>
                    {/if}
                    {#if draft === 'reading_only' && a.thermostat_mode !== 'reading_only'}
                      <Notice tone="warn">Zurückstufen auf „nur lesen“: Der Sollwert am Gerät bleibt stehen und wird von thermoctl nicht mehr angefasst.</Notice>
                    {/if}
                  {/if}
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/each}
    </div>

    {#if data.may_edit}
      <form class="grid gap-3 rounded-xl border bg-card p-4" onsubmit={add}>
        <h3 class="m-0 text-base">Gerät zuordnen</h3>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="grid gap-1 text-sm"><span>Gerät</span>
            <select class="h-11 rounded-lg border bg-background px-3" bind:value={addDevice} required>
              <option value="">Bitte wählen</option>
              {#each data.devices as d (d.id)}<option value={String(d.id)}>{d.name}</option>{/each}
            </select></label>
          <label class="grid gap-1 text-sm"><span>Rolle</span>
            <select class="h-11 rounded-lg border bg-background px-3" bind:value={addRole} required>
              <option value="">Bitte wählen</option>
              {#each data.roles as r (r.id)}<option value={r.code}>{r.label}{needs(r.code) ? ` (braucht: ${needs(r.code)})` : ''}</option>{/each}
            </select></label>
        </div>
        <div><Button type="submit" disabled={busy || !addDevice || !addRole}>Zuordnen</Button></div>
      </form>

      {#if assignedDevices.length}
        <form class="grid gap-3 rounded-xl border bg-card p-4" onsubmit={swap}>
          <h3 class="m-0 text-base">Gerät austauschen</h3>
          <p class="m-0 text-sm text-muted-foreground">Das neue Gerät übernimmt alle Rollen des alten in diesem Raum.</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="grid gap-1 text-sm"><span>Altes Gerät</span>
              <select class="h-11 rounded-lg border bg-background px-3" bind:value={swapOld} onchange={() => (confirmSwap = false)} required>
                <option value="">Bitte wählen</option>
                {#each assignedDevices as [id, name] (id)}<option value={String(id)}>{name}</option>{/each}
              </select></label>
            <label class="grid gap-1 text-sm"><span>Neues Gerät</span>
              <select class="h-11 rounded-lg border bg-background px-3" bind:value={swapNew} onchange={() => (confirmSwap = false)} required>
                <option value="">Bitte wählen</option>
                {#each data.devices.filter((d) => String(d.id) !== swapOld) as d (d.id)}<option value={String(d.id)}>{d.name}</option>{/each}
              </select></label>
          </div>
          {#if confirmSwap}<Notice tone="warn">Der Austausch ändert die Regelung dieses Raums. Zum Bestätigen noch einmal wählen.</Notice>{/if}
          <div><Button type="submit" variant={confirmSwap ? 'destructive' : 'default'} disabled={busy || !swapOld || !swapNew}>{confirmSwap ? 'Wirklich austauschen' : 'Austauschen'}</Button></div>
        </form>
      {/if}
    {/if}
  {/if}
</section>
