<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowLeft, Minus, Pencil, Plus, SkipForward, Trash2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { Button } from '$lib/components/ui/button';
  import { Switch } from '$lib/components/ui/switch';
  import { formatCelsius, formatTemperature, parseTemperatureInput } from '$lib/rooms/decimal';
  import {
    formatAge,
    formatInstant,
    isTemperatureField,
    measurementLine,
    plannedDiffers,
    roomActionsUi,
    roomStatus,
    shouldReload,
    toFailure,
    type Failure,
  } from '$lib/rooms/logic';
  import { createResource } from '$lib/rooms/resource.svelte';
  import type { OverrideEnd, Rooms, RoomDetail } from '$lib/rooms/types';
  import { PERIODS, type PeriodId } from '$lib/history/format';
  import HistoryChart from '$lib/components/history/HistoryChart.svelte';
  import DayTrack from './DayTrack.svelte';
  import HelpTip from './HelpTip.svelte';
  import Notice from './Notice.svelte';
  import RoomJumps from './RoomJumps.svelte';
  import RoomTechnik from '$lib/components/operations/RoomTechnik.svelte';
  import ReportCard from '$lib/components/zones/ReportCard.svelte';
  import RoomDeleteDialog from '$lib/components/zones/RoomDeleteDialog.svelte';
  import RoomFormDialog from '$lib/components/zones/RoomFormDialog.svelte';

  let { id }: { id: number } = $props();
  const { api, prefix, router, session } = useApp();

  interface Page {
    detail: RoomDetail;
    timezone: string;
    nowFraction: number;
  }
  // Der Raum kommt aus /rooms/{id}; Zeitzone der Anlage und Tageslinie stehen nur in der Liste.
  const loadPage = async (): Promise<Page> => {
    // Nacheinander, nicht parallel: jede angemeldete Anfrage schreibt `last_seen_at`; zwei
    // gleichzeitige führen bei SQLite zu „database is locked“ (HTTP 500).
    const detail = await api.get<RoomDetail>(`/ui-api/rooms/${id}`);
    const list = await api.get<Rooms>('/ui-api/rooms');
    return { detail, timezone: list.timezone, nowFraction: list.now_fraction };
  };
  const page = createResource<Page>(loadPage);
  onMount(() => page.start());

  const data = $derived(page.data);
  const room = $derived(data?.detail ?? null);
  const tz = $derived(data?.timezone ?? 'UTC');
  const ui = $derived(room ? roomActionsUi(room, room.next_switch !== null) : null);
  const tenantView = $derived(session.bootstrap?.ui_profile === 'tenant');
  const status = $derived(room ? roomStatus(room, tenantView) : null);
  const line = $derived(room ? measurementLine(room, tenantView) : null);

  // -- Schreibende Aufrufe: einer nach dem anderen, nie automatisch wiederholt ------------------

  let busy = $state<string | null>(null);
  let errors = $state<Record<string, Failure | undefined>>({});
  let done = $state<Record<string, string | undefined>>({});

  function clear(scope: string) {
    errors[scope] = undefined;
    done[scope] = undefined;
  }

  async function run(scope: string, call: () => Promise<RoomDetail>, success: string): Promise<boolean> {
    if (busy !== null || !data) return false;
    busy = scope;
    clear(scope);
    try {
      const detail = await call();
      page.adopt({ ...data, detail });
      done[scope] = success;
      return true;
    } catch (error) {
      const failure = toFailure(error);
      errors[scope] = failure;
      if (shouldReload(failure)) void page.refresh();
      return false;
    } finally {
      busy = null;
    }
  }

  const base = $derived(`/ui-api/rooms/${id}`);

  // Stepper
  function step(direction: 'up' | 'down') {
    const modeId = room?.setpoint.mode_id;
    if (modeId === null || modeId === undefined) return;
    void run('step', () => api.post<RoomDetail>(`${base}/setpoint-step`, { mode_id: modeId, direction }), 'Sollwert geändert.');
  }

  // Übersteuerung (Entwurf bleibt bei Fehlern erhalten)
  let overrideTemp = $state<string | null>(null);
  let overrideEnd = $state<'next_switch' | 'duration'>('next_switch');
  let permanent = $state(false);
  let overrideMinutes = $state('60');

  const overrideTempText = $derived(overrideTemp ?? (room ? formatTemperature(room.setpoint.temperature_c) : ''));

  async function createOverride(event: SubmitEvent) {
    event.preventDefault();
    const parsed = parseTemperatureInput(overrideTempText);
    if (!parsed.ok) {
      errors['override'] = { kind: 'validation', message: parsed.message, field: 'temperature_c', code: null };
      return;
    }
    const end: OverrideEnd = permanent ? 'permanent' : overrideEnd;
    const body: { temperature_c: string; end: OverrideEnd; duration_minutes?: number } = {
      temperature_c: parsed.value,
      end,
    };
    if (end === 'duration') {
      const minutes = Number(overrideMinutes);
      if (!/^\d+$/.test(overrideMinutes.trim()) || !Number.isSafeInteger(minutes) || minutes <= 0) {
        errors['override'] = { kind: 'validation', message: 'Bitte eine Dauer in ganzen Minuten angeben.', field: 'duration_minutes', code: null };
        return;
      }
      body.duration_minutes = minutes;
    }
    const ok = await run('override', () => api.post<RoomDetail>(`${base}/override`, body), 'Übersteuerung gesetzt.');
    if (ok) overrideTemp = null;
  }

  const cancelOverride = () =>
    run('cancel', () => api.post<RoomDetail>(`${base}/override/cancel`, {}), 'Übersteuerung beendet. Es gilt wieder der Plan.');

  // Nächste Phase
  let askReplace = $state(false);
  async function jump(replace: boolean) {
    const ok = await run('jump', () => api.post<RoomDetail>(`${base}/jump-next`, { replace }), 'Nächste Phase gilt jetzt.');
    askReplace = !ok && errors['jump']?.code === 'override_running';
    if (ok) askReplace = false;
  }

  // Modus-Sollwerte
  let drafts = $state<Record<number, string | undefined>>({});
  const modeText = (modeId: number, stored: string | null) => drafts[modeId] ?? (stored === null ? '' : formatTemperature(stored));
  async function saveMode(modeId: number, remove: boolean) {
    const scope = `mode-${modeId}`;
    let value: string | null = null;
    if (!remove) {
      const parsed = parseTemperatureInput(drafts[modeId] ?? '');
      if (!parsed.ok) {
        errors[scope] = { kind: 'validation', message: parsed.message, field: 'temperature_c', code: null };
        return;
      }
      value = parsed.value;
    }
    const ok = await run(scope, () => api.put<RoomDetail>(`${base}/modes/${modeId}/setpoint`, { temperature_c: value }), remove ? 'Temperatur entfernt.' : 'Gespeichert.');
    if (ok) drafts[modeId] = undefined;
  }

  // Betriebsart
  let pendingOff = $state<string | null>(null);
  async function setOperatingMode(code: string) {
    if (!room || code === room.operating_mode.code) return;
    if (code === 'off' && pendingOff !== code) {
      pendingOff = code;
      return;
    }
    pendingOff = null;
    await run('opmode', () => api.put<RoomDetail>(`${base}/operating-mode`, { code }), 'Betriebsart geändert.');
  }

  // Raum bearbeiten / löschen (Dialoge laden ihre Daten selbst; der Server entscheidet über Rechte)
  let editOpen = $state(false);
  let deleteOpen = $state(false);
  let roomNotice = $state<string | null>(null);

  // Verlauf des Raums: derselbe Baustein wie unter Auswertung, Raum fest, Zeitraum wählbar.
  let historyPeriod = $state<PeriodId>('24h');

  // Mobil: zwei Ansichten
  let view = $state<'control' | 'tech'>('control');

  const fieldInvalid = (scope: string, field: string) =>
    field === 'temperature_c' ? isTemperatureField(errors[scope]?.field ?? null) : errors[scope]?.field === field;
  const card = 'rounded-xl border bg-card p-4 sm:p-5';
  const inputClass = 'h-10 w-full rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive';
</script>

<nav class="mb-3" aria-label="Brotkrumen">
  <a class="inline-flex min-h-10 items-center gap-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground" href={appHref(prefix, '/')}>
    <ArrowLeft size={16} aria-hidden="true" />Übersicht
  </a>
</nav>

{#if page.loading && !room}
  <p class="text-muted-foreground" role="status">Raum wird geladen …</p>
{:else if !room || !ui || !data}
  <div class="max-w-xl space-y-3">
    <h1>{page.failure?.kind === 'not_found' ? 'Raum nicht gefunden' : 'Raum nicht verfügbar'}</h1>
    <Notice tone="error">{page.failure?.message ?? 'Der Raum konnte nicht geladen werden.'}</Notice>
    {#if page.failure?.kind !== 'not_found'}
      <Button variant="outline" onclick={() => page.refresh()}>Erneut versuchen</Button>
    {/if}
  </div>
{:else}
  <header class="mb-4 flex flex-wrap items-center gap-3">
    <h1 class="m-0">{room.name}</h1>
    {#if status}
      <span class="rounded-full px-2.5 py-0.5 text-xs font-medium {status.attention ? 'bg-destructive/10 text-destructive' : 'bg-secondary'}">{status.label}</span>
    {/if}
    {#if ui.operatingMode.show}
      <span class="ml-auto flex gap-2">
        <Button variant="outline" onclick={() => { roomNotice = null; editOpen = true; }} data-testid="room-edit"><Pencil aria-hidden="true" />Bearbeiten</Button>
        <Button variant="outline" onclick={() => { roomNotice = null; deleteOpen = true; }} data-testid="room-delete"><Trash2 aria-hidden="true" />Löschen</Button>
      </span>
    {/if}
  </header>

  <RoomJumps roomId={id} />

  {#if roomNotice}<div class="mb-4"><Notice tone="success">{roomNotice}</Notice></div>{/if}

  {#if page.failure}
    <div class="mb-4"><Notice tone="warn">Aktualisieren fehlgeschlagen: {page.failure.message} Angezeigt ist der letzte bekannte Stand.</Notice></div>
  {/if}

  <div class="mb-4 flex gap-1 rounded-lg bg-muted p-1 lg:hidden" role="tablist" aria-label="Raumansicht">
    {#each [['control', 'Bedienung'], ['tech', 'Technik']] as [key, label] (key)}
      <button
        type="button"
        role="tab"
        id={`tab-${key}`}
        aria-selected={view === key}
        aria-controls={`panel-${key}`}
        class="min-h-10 flex-1 rounded-md px-3 text-sm font-medium {view === key ? 'bg-background shadow-sm' : 'text-muted-foreground'}"
        onclick={() => (view = key as 'control' | 'tech')}>{label}</button
      >
    {/each}
  </div>

  <div class="grid items-start gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
    <!-- Bedienung -->
    <div id="panel-control" role="tabpanel" aria-labelledby="tab-control" class="space-y-5 {view === 'tech' ? 'hidden lg:block lg:space-y-5' : ''}">
      <section class={card} aria-labelledby="now-title">
        <h2 id="now-title" class="sr-only">Jetzt</h2>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <div class="text-xs text-muted-foreground">Ist</div>
            <div class="text-5xl leading-none font-semibold tracking-tight tabular-nums" data-testid="ist" aria-label={`Ist ${formatCelsius(room.temperature_c)}`}>
              {formatTemperature(room.temperature_c)}<span class="ml-1 text-xl font-normal text-muted-foreground">°C</span>
            </div>
            <div class="mt-1 text-xs {line?.problem ? 'text-destructive' : 'text-muted-foreground'}" data-testid="measurement-line">
              {line?.text}
            </div>
          </div>
          <div>
            <div class="flex items-center gap-1 text-xs text-muted-foreground">Soll (wirksam)<HelpTip term="setpoint" /></div>
            <div class="text-5xl leading-none font-semibold tracking-tight tabular-nums" data-testid="soll">
              {formatTemperature(room.setpoint.temperature_c)}<span class="ml-1 text-xl font-normal text-muted-foreground">°C</span>
            </div>
            <div class="mt-1 text-xs text-muted-foreground">{room.setpoint.mode_name ?? room.setpoint.reason}</div>
            {#if plannedDiffers(room)}
              <div class="mt-1 flex items-center gap-1 text-xs text-muted-foreground" data-testid="plan">
                Plan: {formatCelsius(room.decision?.planned_setpoint_c)}<HelpTip term="planned" />
              </div>
            {/if}
          </div>
        </div>
        <div class="mt-4"><DayTrack segments={room.day_track} nowFraction={data.nowFraction} /></div>
        <div class="mt-1 flex items-center gap-1 text-xs text-muted-foreground">Tagesverlauf ({tz})<HelpTip term="day_track" /></div>
      </section>

      {#if ui.step.show}
        <section class={card} aria-labelledby="step-title">
          <h2 id="step-title" class="mb-3 flex items-center gap-1 text-base">Sollwert anpassen<HelpTip term="step" /></h2>
          <div class="flex items-center justify-center gap-4">
            <Button variant="outline" size="icon-lg" class="size-12" aria-label="Sollwert um ein halbes Grad senken" disabled={ui.step.disabled || busy !== null} onclick={() => step('down')}><Minus /></Button>
            <output class="min-w-24 text-center text-3xl font-semibold tabular-nums" aria-live="polite" data-testid="step-value">{formatTemperature(room.setpoint.temperature_c)}°</output>
            <Button variant="outline" size="icon-lg" class="size-12" aria-label="Sollwert um ein halbes Grad erhöhen" disabled={ui.step.disabled || busy !== null} onclick={() => step('up')}><Plus /></Button>
          </div>
          {#if ui.step.reason}<p class="mt-3 mb-0 text-center text-sm text-muted-foreground">{ui.step.reason}</p>{/if}
          <div class="mt-3 space-y-2">
            {#if errors['step']}<Notice tone="error">{errors['step'].message}</Notice>{/if}
            {#if done['step']}<Notice tone="success">{done['step']}</Notice>{/if}
          </div>
        </section>
      {/if}

      {#if ui.createOverride.show || ui.cancelOverride.show || room.override}
        <section class={card} aria-labelledby="override-title">
          <h2 id="override-title" class="mb-3 flex items-center gap-1 text-base">Befristete Übersteuerung<HelpTip term="override" /></h2>
          {#if room.override}
            <p class="mb-3" data-testid="override-active">
              Läuft: <strong>{formatCelsius(room.override.temperature_c ?? room.setpoint.temperature_c)}</strong>
              {room.override.ends_at ? `bis ${formatInstant(room.override.ends_at, tz)}` : 'ohne Ende'}.
            </p>
            {#if ui.cancelOverride.show}
              <Button variant="outline" disabled={busy !== null} onclick={cancelOverride}>Übersteuerung beenden</Button>
            {/if}
          {:else if ui.createOverride.show}
            <form class="space-y-4" onsubmit={createOverride} novalidate>
              <div>
                <label class="mb-1 block text-sm font-medium" for="ov-temp">Temperatur in °C</label>
                <input
                  id="ov-temp"
                  class={inputClass}
                  inputmode="decimal"
                  autocomplete="off"
                  value={overrideTempText}
                  oninput={(e) => (overrideTemp = e.currentTarget.value)}
                  aria-invalid={fieldInvalid('override', 'temperature_c')}
                  aria-describedby="ov-hint"
                  disabled={ui.createOverride.disabled}
                />
                <p id="ov-hint" class="mt-1 mb-0 text-xs text-muted-foreground">Erlaubt: {formatCelsius(room.minimum_temperature_c)} bis {formatCelsius(room.maximum_temperature_c)}.</p>
              </div>
              <div class="flex items-center justify-between gap-3 rounded-lg bg-muted/60 px-3 py-2">
                <label for="ov-permanent" class="text-sm font-medium">Unbefristet halten</label>
                <Switch id="ov-permanent" bind:checked={permanent} disabled={ui.createOverride.disabled} />
              </div>
              {#if !permanent}
                <fieldset class="m-0 space-y-2 border-0 p-0">
                  <legend class="mb-1 text-sm font-medium">Bis wann?</legend>
                  <label class="flex min-h-10 items-center gap-2 text-sm {room.next_switch ? '' : 'text-muted-foreground'}">
                    <input type="radio" name="ov-end" value="next_switch" bind:group={overrideEnd} disabled={!room.next_switch} />
                    Bis zur nächsten Phase{room.next_switch ? ` (${formatInstant(room.next_switch.at, tz)})` : ' (kein Plan)'}
                  </label>
                  <label class="flex min-h-10 flex-wrap items-center gap-2 text-sm">
                    <input type="radio" name="ov-end" value="duration" bind:group={overrideEnd} />
                    Für
                    <input
                      class="h-9 w-20 rounded-lg border bg-background px-2 text-sm aria-[invalid=true]:border-destructive"
                      inputmode="numeric"
                      aria-label="Dauer in Minuten"
                      bind:value={overrideMinutes}
                      aria-invalid={fieldInvalid('override', 'duration_minutes')}
                      onfocus={() => (overrideEnd = 'duration')}
                    />
                    Minuten
                  </label>
                </fieldset>
              {/if}
              <Button type="submit" disabled={busy !== null || ui.createOverride.disabled}>{busy === 'override' ? 'Wird gesetzt …' : 'Übersteuerung setzen'}</Button>
            </form>
          {/if}
          <div class="mt-3 space-y-2">
            {#if errors['override']}<Notice tone="error">{errors['override'].message}</Notice>{/if}
            {#if errors['cancel']}<Notice tone="error">{errors['cancel'].message}</Notice>{/if}
            {#if done['override']}<Notice tone="success">{done['override']}</Notice>{/if}
            {#if done['cancel']}<Notice tone="success">{done['cancel']}</Notice>{/if}
          </div>
        </section>
      {/if}

      {#if ui.jumpNext.show}
        <section class={card} aria-labelledby="next-title">
          <h2 id="next-title" class="mb-2 flex items-center gap-1 text-base">Nächste Phase<HelpTip term="jump_next" /></h2>
          {#if room.next_switch}
            <p class="mb-3" data-testid="next-switch">
              {formatInstant(room.next_switch.at, tz)}: {room.next_switch.mode_name ?? room.next_switch.reason}, {formatCelsius(room.next_switch.temperature_c)}.
            </p>
          {:else}
            <p class="mb-3 text-muted-foreground">Kein weiterer Schaltpunkt im Plan.</p>
          {/if}
          <Button variant="outline" disabled={ui.jumpNext.disabled || busy !== null} onclick={() => jump(false)}><SkipForward />Jetzt vorziehen</Button>
          {#if ui.jumpNext.reason}<p class="mt-2 mb-0 text-sm text-muted-foreground">{ui.jumpNext.reason}</p>{/if}
          <div class="mt-3 space-y-2">
            {#if errors['jump']}<Notice tone="error">{errors['jump'].message}</Notice>{/if}
            {#if askReplace}
              <div class="flex flex-wrap gap-2">
                <Button size="sm" disabled={busy !== null} onclick={() => jump(true)}>Übersteuerung ersetzen</Button>
                <Button size="sm" variant="outline" onclick={() => (askReplace = false)}>Abbrechen</Button>
              </div>
            {/if}
            {#if done['jump']}<Notice tone="success">{done['jump']}</Notice>{/if}
          </div>
        </section>
      {/if}

      {#if ui.operatingMode.show}
        <section class={card} aria-labelledby="opmode-title">
          <h2 id="opmode-title" class="mb-3 flex items-center gap-1 text-base">Betriebsart<HelpTip term="operating_mode" /></h2>
          <div class="flex flex-wrap gap-2" role="group" aria-labelledby="opmode-title">
            {#each room.operating_modes as kind (kind.code)}
              <Button
                variant={room.operating_mode.code === kind.code ? 'secondary' : 'outline'}
                aria-pressed={room.operating_mode.code === kind.code}
                disabled={busy !== null}
                onclick={() => setOperatingMode(kind.code)}>{kind.label}</Button
              >
            {/each}
          </div>
          {#if pendingOff}
            <div class="mt-3 space-y-2">
              <Notice tone="warn">Raum ausschalten? Er hält dann nur noch den Frostschutz.</Notice>
              <div class="flex gap-2">
                <Button size="sm" disabled={busy !== null} onclick={() => setOperatingMode(pendingOff!)}>Ausschalten</Button>
                <Button size="sm" variant="outline" onclick={() => (pendingOff = null)}>Abbrechen</Button>
              </div>
            </div>
          {/if}
          <div class="mt-3 space-y-2">
            {#if errors['opmode']}<Notice tone="error">{errors['opmode'].message}</Notice>{/if}
            {#if done['opmode']}<Notice tone="success">{done['opmode']}</Notice>{/if}
          </div>
        </section>
      {/if}

      <section aria-labelledby="room-history-title" data-testid="room-history">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 id="room-history-title" class="m-0 text-base">Verlauf</h2>
          <div class="flex gap-1" role="group" aria-label="Zeitraum des Verlaufs">
            {#each PERIODS as p (p.id)}
              <Button variant={historyPeriod === p.id ? 'secondary' : 'outline'} size="sm" class="min-h-9" aria-pressed={historyPeriod === p.id} onclick={() => (historyPeriod = p.id)}>{p.label}</Button>
            {/each}
          </div>
        </div>
        <HistoryChart zones={[{ id: room.id, name: room.name }]} zoneId={room.id} period={historyPeriod} filters={false} compact lazy heading="h3" title="Temperaturverlauf" />
      </section>

      <ReportCard zoneId={room.id} hideWhenEmpty />
    </div>

    <!-- Technik -->
    <div id="panel-tech" role="tabpanel" aria-labelledby="tab-tech" class="space-y-3 {view === 'control' ? 'hidden lg:block lg:space-y-3' : ''}">
      <details class={card} open>
        <summary class="flex cursor-pointer items-center gap-1 text-base font-semibold">Sensor<HelpTip term="sensor" /></summary>
        <dl class="mt-3 mb-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt class="text-muted-foreground">Status</dt><dd class="m-0">{tenantView && room.measurement_stale ? 'Möglicherweise nicht aktuell' : room.sensor.status_label}</dd>
          <dt class="text-muted-foreground">Gemessen</dt><dd class="m-0">{formatInstant(room.measured_at, tz)} ({formatAge(room.measured_age_seconds)})</dd>
          <dt class="text-muted-foreground">Fenster</dt><dd class="m-0">{room.sensor.window_alarm ? 'Alarm' : room.sensor.window_open ? 'offen' : room.sensor.window_open === false ? 'geschlossen' : 'unbekannt'}{room.sensor.window_open_by_temperature ? ' (aus Temperaturverlauf erkannt)' : ''}</dd>
          <dt class="text-muted-foreground">Messwert hängt</dt><dd class="m-0">{room.sensor.stuck ? 'ja' : 'nein'}</dd>
        </dl>
      </details>

      <details class={card} open>
        <summary class="flex cursor-pointer items-center gap-1 text-base font-semibold">Regelentscheidung<HelpTip term="decision" /></summary>
        {#if room.decision}
          <dl class="mt-3 mb-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt class="text-muted-foreground flex items-center gap-1">Ergebnis<HelpTip term="heat_request" /></dt><dd class="m-0">{room.decision.would_heat ? 'Heizanforderung' : 'Keine Heizanforderung'}</dd>
            <dt class="text-muted-foreground">Begründung</dt><dd class="m-0">{room.decision.reason}</dd>
            <dt class="text-muted-foreground">Entschieden</dt><dd class="m-0">{formatInstant(room.decision.decided_at, tz)}</dd>
          </dl>
        {:else}
          <p class="mt-3 mb-0 text-sm text-muted-foreground">Noch keine Entscheidung.</p>
        {/if}
        {#if room.emergency}
          <div class="mt-3"><Notice tone="warn"><strong>{room.emergency.headline}</strong> {room.emergency.detail}</Notice></div>
        {/if}
      </details>

      <details class={card} open>
        <summary class="flex cursor-pointer items-center gap-1 text-base font-semibold">Temperaturen je Modus<HelpTip term="mode_setpoints" /></summary>
        <ul class="mt-3 mb-0 list-none space-y-3 p-0">
          {#each room.modes as mode (mode.mode_id)}
            {@const scope = `mode-${mode.mode_id}`}
            <li>
              <form
                class="flex flex-wrap items-center gap-2"
                onsubmit={(e) => {
                  e.preventDefault();
                  void saveMode(mode.mode_id, false);
                }}
                novalidate
              >
                <label for={`m-${mode.mode_id}`} class="min-w-20 flex-1 text-sm {mode.is_current ? 'font-semibold' : ''}">{mode.name}{mode.is_current ? ' (aktiv)' : ''}</label>
                {#if ui.modeSetpoints.show}
                  <input
                    id={`m-${mode.mode_id}`}
                    class="h-10 w-24 rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive"
                    inputmode="decimal"
                    autocomplete="off"
                    placeholder="—"
                    value={modeText(mode.mode_id, mode.temperature_c)}
                    oninput={(e) => (drafts[mode.mode_id] = e.currentTarget.value)}
                    aria-invalid={fieldInvalid(scope, 'temperature_c')}
                  />
                  <span class="text-sm text-muted-foreground">°C</span>
                  <span class="flex w-[11.5rem] gap-1">
                    <Button type="submit" size="sm" class="min-h-9" disabled={busy !== null || drafts[mode.mode_id] === undefined}>Speichern</Button>
                    {#if mode.temperature_c !== null}
                      <Button type="button" size="sm" variant="ghost" class="min-h-9" disabled={busy !== null} onclick={() => saveMode(mode.mode_id, true)}>Entfernen</Button>
                    {/if}
                  </span>
                {:else}
                  <span id={`m-${mode.mode_id}`} class="text-sm tabular-nums">{formatCelsius(mode.temperature_c)}</span>
                {/if}
              </form>
              {#if errors[scope]}<div class="mt-2"><Notice tone="error">{errors[scope]!.message}</Notice></div>{/if}
              {#if done[scope]}<div class="mt-2"><Notice tone="success">{done[scope]}</Notice></div>{/if}
            </li>
          {/each}
        </ul>
      </details>

      <RoomTechnik zoneId={room.id} />

      {#if room.vacation}
        <details class={card}>
          <summary class="cursor-pointer text-base font-semibold">Urlaub der Anlage</summary>
          <p class="mt-3 mb-0 text-sm">
            {room.vacation.running ? 'Läuft seit' : 'Beginnt'} {formatInstant(room.vacation.starts_at, tz)}, bis {formatInstant(room.vacation.ends_at, tz)}; Absenkung {formatCelsius(room.vacation.setback_temperature_c)}.
          </p>
        </details>
      {/if}
    </div>
  </div>
{/if}

{#if room}
  <RoomFormDialog
    bind:open={editOpen}
    zoneId={room.id}
    ondone={(saved) => {
      roomNotice = `Raum „${saved.display_name}“ gespeichert.`;
      void page.refresh();
    }}
  />
  <RoomDeleteDialog
    bind:open={deleteOpen}
    zoneId={room.id}
    ondone={() => router.navigate('/uebersicht/raeume')}
  />
{/if}
