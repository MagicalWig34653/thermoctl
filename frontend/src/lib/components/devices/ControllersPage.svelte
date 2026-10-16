<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { deviceFailure, failureText } from '$lib/devices/errors';
  import type { Controller, ControllerChannel, Controllers } from '$lib/devices/types';
  import { lastValueLine } from '$lib/devices/last-value';
  import { formatInstant } from '$lib/rooms/logic';

  const { api } = useApp();
  const client = createDevicesClient(api);

  let data = $state<Controllers | null>(null);
  let loadError = $state<string | null>(null);
  let busy = $state(false);
  let notice = $state<{ tone: 'error' | 'success' | 'warn'; text: string; where: string } | null>(null);
  let generation = 0;

  async function load() {
    const mine = ++generation;
    try {
      const value = await client.controllers();
      if (mine !== generation) return;
      seed(value);
      data = value;
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

  async function run(where: string, action: () => Promise<Controllers>, ok: string) {
    if (busy) return;
    busy = true;
    notice = null;
    try {
      generation += 1;
      const next = await action();
      seed(next);
      data = next;
      notice = { tone: 'success', text: ok, where };
    } catch (error) {
      const f = deviceFailure(error);
      notice = { tone: f.isConflict ? 'warn' : 'error', text: failureText(f), where };
      if (f.reload) await load();
    } finally {
      busy = false;
    }
  }

  // -- Kanäle ------------------------------------------------------------------------------
  const READ_KINDS = ['zone_setpoint', 'operating_mode'];
  const WRITE_KINDS = ['sensor_temperature', 'zone_temperature', 'zone_setpoint', 'fixed'];
  let buttons = $state<Record<string, { command: string; step: string }>>({});
  const bkey = (d: Controller, action: string) => `${d.id}|${action}`;
  type Draft = { kind: string; zone: string; source: string; text: string; number: string };
  let drafts = $state<Record<string, Draft>>({});
  const key = (d: Controller, prop: string, dir: string) => `${d.id}|${prop}|${dir}`;

  function channelOf(d: Controller, prop: string, dir: string): ControllerChannel | undefined {
    return d.channels.find((c) => c.property_name === prop && c.direction === dir);
  }
  // Entwürfe werden nach jedem Laden angelegt, nie beim Zeichnen (dort wäre das Schreiben in
  // den Zustand verboten). Bereits Eingegebenes bleibt erhalten.
  function seed(value: Controllers) {
    for (const d of value.controllers) {
      for (const p of d.properties) {
        for (const dir of ['read', 'write'] as const) {
          const k = key(d, p.name, dir);
          if (drafts[k]) continue;
          const c = channelOf(d, p.name, dir);
          drafts[k] = {
            kind: c?.kind ?? (dir === 'read' ? READ_KINDS[0] : WRITE_KINDS[0]),
            zone: c?.zone_id != null ? String(c.zone_id) : '',
            source: c?.source_device_id != null ? String(c.source_device_id) : '',
            text: c?.fixed_text ?? '',
            number: c?.fixed_number ?? '',
          };
        }
      }
      for (const b of d.bindings) {
        const k = bkey(d, b.action);
        if (!buttons[k]) buttons[k] = { command: b.command_code ?? '', step: b.step_k ?? '' };
      }
    }
  }
  const draftOf = (d: Controller, prop: string, dir: string): Draft => drafts[key(d, prop, dir)];
  const kindLabel = (code: string) => data?.kinds.find((k) => k.code === code)?.label ?? code;
  const manageableZones = $derived(data?.zones.filter((z) => z.manageable) ?? []);

  function saveChannel(d: Controller, prop: string, dir: 'read' | 'write') {
    const v = draftOf(d, prop, dir);
    return run(
      key(d, prop, dir),
      () =>
        client.channel({
          device_id: d.id, property_name: prop, direction: dir, kind: v.kind,
          zone_id: v.zone ? Number(v.zone) : null,
          source_device_id: dir === 'write' && v.source ? Number(v.source) : null,
          fixed_text: dir === 'write' && v.text.trim() ? v.text.trim() : null,
          fixed_number: dir === 'write' && v.number.trim() ? v.number.trim() : null,
        }),
      'Gespeichert.',
    );
  }

  // -- Tasten ------------------------------------------------------------------------------
  const buttonDraft = (d: Controller, action: string) => buttons[bkey(d, action)];
  function saveButton(d: Controller, action: string) {
    const v = buttonDraft(d, action);
    return run(bkey(d, action), () => client.button({ device_id: d.id, action_code: action, command: v.command || null, step_k: v.step.trim() || null }), 'Tastenbelegung übernommen.');
  }
  const input = 'h-11 min-w-0 rounded-lg border bg-background px-3';
</script>

<header class="mb-6">
  <h1>Bediengeräte</h1>
  <p class="text-muted-foreground">Je Merkmal: was vom Gerät ankommt und was thermoctl dorthin sendet, dazu die Belegung der Tasten.</p>
</header>

{#if loadError && !data}
  <Notice tone="error">{loadError}</Notice>
{:else if !data}
  <p class="text-muted-foreground" role="status">Wird geladen …</p>
{:else}
  {#if loadError}<div class="mb-3"><Notice tone="error">{loadError}</Notice></div>{/if}
  {#if !data.controllers.length}
    <p class="text-muted-foreground">Noch kein Bediengerät ist einem sichtbaren Raum zugeordnet.</p>
  {/if}
  <div class="grid gap-6">
    {#each data.controllers as d (d.id)}
      <section class="grid gap-4 rounded-xl border bg-card p-4" aria-labelledby={`ctl-${d.id}`} data-testid="controller">
        <div>
          <h2 id={`ctl-${d.id}`} class="m-0">{d.name}</h2>
          <p class="m-0 text-sm text-muted-foreground">Räume: {d.zones.length ? d.zones.map((z) => z.name).join(', ') : '—'}{#if !d.manageable} · nur Ansicht{/if}</p>
        </div>

        <div>
          <h3 class="m-0 text-base">Was ankommt</h3>
          {#each d.properties.filter((p) => p.readable) as p (p.id)}
            {@const v = draftOf(d, p.name, 'read')}
            <div class="mt-2 grid gap-2 rounded-lg border p-3" data-testid="channel-read">
              <div class="font-medium">{p.name}<span class="text-sm font-normal text-muted-foreground"> · {p.value_type}{p.unit ? ` (${p.unit})` : ''}</span></div>
              <div class="text-sm" data-testid="last-value"><span class="mr-1 text-muted-foreground">Letzter Wert:</span>{lastValueLine(p, data.timezone)}</div>
              {#if d.manageable}
                <div class="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                  <label class="grid gap-1 text-sm"><span>Verwendung</span>
                    <select class={input} bind:value={v.kind}>{#each READ_KINDS as k (k)}<option value={k}>{kindLabel(k)}</option>{/each}</select></label>
                  <label class="grid gap-1 text-sm"><span>Raum</span>
                    <select class={input} bind:value={v.zone}><option value="">Bitte wählen</option>{#each manageableZones as z (z.id)}<option value={String(z.id)}>{z.name}</option>{/each}</select></label>
                  <Button variant="outline" disabled={busy || !v.zone} onclick={() => saveChannel(d, p.name, 'read')}>Speichern</Button>
                </div>
              {:else}
                <div class="text-sm">{channelOf(d, p.name, 'read') ? kindLabel(channelOf(d, p.name, 'read')!.kind) : 'Nicht belegt'}</div>
              {/if}
              {#if notice?.where === key(d, p.name, 'read')}<Notice tone={notice.tone}>{notice.text}</Notice>{/if}
            </div>
          {:else}
            <p class="m-0 mt-1 text-sm text-muted-foreground">Keine lesbaren Merkmale.</p>
          {/each}
        </div>

        <div>
          <h3 class="m-0 text-base">Was hingeschickt wird</h3>
          {#each d.properties.filter((p) => p.writable) as p (p.id)}
            {@const v = draftOf(d, p.name, 'write')}
            <div class="mt-2 grid gap-2 rounded-lg border p-3" data-testid="channel-write">
              <div class="font-medium">{p.name}
                <span class="text-sm font-normal text-muted-foreground"> · erlaubt: {p.values.length ? p.values.join(', ') : p.min_value !== null || p.max_value !== null ? `${p.min_value ?? '…'} bis ${p.max_value ?? '…'} ${p.unit ?? ''}` : p.value_type}</span></div>
              {#if d.manageable}
                <div class="grid gap-2 sm:grid-cols-2">
                  <label class="grid gap-1 text-sm"><span>Quelle</span>
                    <select class={input} bind:value={v.kind}>{#each WRITE_KINDS as k (k)}<option value={k}>{kindLabel(k)}</option>{/each}</select></label>
                  <label class="grid gap-1 text-sm"><span>Raum</span>
                    <select class={input} bind:value={v.zone}><option value="">Kein Raum</option>{#each manageableZones as z (z.id)}<option value={String(z.id)}>{z.name}</option>{/each}</select></label>
                  <label class="grid gap-1 text-sm"><span>Quellgerät</span>
                    <select class={input} bind:value={v.source}><option value="">Kein Gerät</option>{#each data.source_devices as s (s.id)}<option value={String(s.id)}>{s.name}</option>{/each}</select></label>
                  <div class="grid grid-cols-2 gap-2">
                    <label class="grid gap-1 text-sm"><span>Fester Text</span><input class={input} bind:value={v.text} /></label>
                    <label class="grid gap-1 text-sm"><span>Feste Zahl</span><input class={input} inputmode="decimal" bind:value={v.number} /></label>
                  </div>
                </div>
                <div><Button variant="outline" disabled={busy} onclick={() => saveChannel(d, p.name, 'write')}>Speichern</Button></div>
              {:else}
                <div class="text-sm">{channelOf(d, p.name, 'write') ? kindLabel(channelOf(d, p.name, 'write')!.kind) : 'Nicht belegt'}</div>
              {/if}
              {#if notice?.where === key(d, p.name, 'write')}<Notice tone={notice.tone}>{notice.text}</Notice>{/if}
            </div>
          {:else}
            <p class="m-0 mt-1 text-sm text-muted-foreground">Keine beschreibbaren Merkmale.</p>
          {/each}
        </div>

        <div>
          <h3 class="m-0 text-base">Tastenbelegung</h3>
          {#if !d.bindings.length}
            <p class="m-0 mt-1 text-sm text-muted-foreground">Noch keinen Tastendruck empfangen.</p>
          {/if}
          {#each d.bindings as b (b.action)}
            {@const v = buttonDraft(d, b.action)}
            <div class="mt-2 grid gap-2 rounded-lg border p-3" data-testid="binding">
              <div class="font-medium">Taste {b.action}{#if b.last_seen}<span class="text-sm font-normal text-muted-foreground"> · zuletzt {formatInstant(b.last_seen, data.timezone)}</span>{/if}</div>
              {#if d.manageable}
                <div class="grid gap-2 sm:grid-cols-[1fr_8rem_auto] sm:items-end">
                  <label class="grid gap-1 text-sm"><span>Aktion</span>
                    <select class={input} bind:value={v.command}><option value="">— nichts —</option>{#each data.commands as c (c.id)}<option value={c.code}>{c.label}</option>{/each}</select></label>
                  <label class="grid gap-1 text-sm"><span>Schrittweite (K)</span><input class={input} inputmode="decimal" placeholder="0,5" bind:value={v.step} /></label>
                  <Button variant="outline" disabled={busy} onclick={() => saveButton(d, b.action)}>Übernehmen</Button>
                </div>
              {:else}
                <div class="text-sm">{b.command_name ?? 'Nicht belegt'}{b.step_k ? ` · ${b.step_k} K` : ''}</div>
              {/if}
              {#if notice?.where === bkey(d, b.action)}<Notice tone={notice.tone}>{notice.text}</Notice>{/if}
            </div>
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}
