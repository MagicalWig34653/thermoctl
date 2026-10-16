<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { runtimePrefix } from '$lib/api';
  import { createAppearance, type Theme } from '$lib/appearance.svelte';
  import { formatTemperature } from '$lib/rooms/decimal';
  import SourceLink from '$lib/components/shell/SourceLink.svelte';
  import { createKioskClient } from './client';
  import { createKiosk } from './controller.svelte';
  import { ageText, clockText, heatText, INVALID_TEXT, overrideText } from './logic';
  import type { KioskRoom, KioskState } from './types';
  import { initialView, KIOSK_VIEWS, normalizeView, resolveView, RETURN_MS, VIEW_STORAGE_KEY, type KioskView } from './view';

  // Eigenständig: keine App-Hülle, kein Bootstrap der Hauptanwendung, keine Anmeldung.
  const prefix = runtimePrefix();
  const appearance = createAppearance();
  const kiosk = createKiosk({
    client: createKioskClient({ fetch: window.fetch.bind(window), prefix, cookies: () => document.cookie }),
    isHidden: () => document.hidden,
  });

  onMount(() => {
    document.title = 'Kiosk – thermoctl';
    kiosk.start();
    return () => kiosk.stop();
  });

  const THEMES: { id: Theme; label: string }[] = [
    { id: 'system', label: 'System' },
    { id: 'light', label: 'Hell' },
    { id: 'dark', label: 'Dunkel' },
  ];
  const nextTheme = () => {
    const i = THEMES.findIndex((t) => t.id === appearance.theme);
    appearance.setTheme(THEMES[(i + 1) % THEMES.length].id);
  };
  const themeLabel = $derived(THEMES.find((t) => t.id === appearance.theme)?.label ?? 'System');

  const data = $derived(kiosk.data);

  // -- Ansicht: auto / panel / tafel (wie am Alt-Kiosk) ----------------------------------------
  function readStored(): string | null {
    try {
      return window.localStorage.getItem(VIEW_STORAGE_KEY);
    } catch {
      return null;
    }
  }
  function remember(value: KioskView) {
    try {
      window.localStorage.setItem(VIEW_STORAGE_KEY, value);
    } catch {
      // Ohne Speicher gilt die Wahl nur bis zum Neuladen.
    }
  }
  const start = initialView(window.location.search, readStored());
  let view = $state<KioskView>(start.view);
  if (start.remember) remember(start.view);
  let width = $state(window.innerWidth);
  const resolved = $derived(resolveView(view, width));
  function chooseView(next: KioskView) {
    view = normalizeView(next);
    remember(view);
    closeRoom();
  }

  // Panel: ein Raum ist flächendeckend offen; ohne Bedienung springt die Ansicht zurück.
  let openId = $state<number | null>(null);
  let backButton = $state<HTMLButtonElement | null>(null);
  let opener: HTMLElement | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const openRoomData = $derived(openId === null ? null : (data?.rooms.find((r) => r.id === openId) ?? null));

  function clearIdle() {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = null;
  }
  function touchIdle() {
    clearIdle();
    idleTimer = setTimeout(closeRoom, RETURN_MS);
  }
  function openRoom(id: number, from: HTMLElement) {
    opener = from;
    openId = id;
    touchIdle();
    void tick().then(() => backButton?.focus());
  }
  function closeRoom() {
    clearIdle();
    const wasOpen = openId !== null;
    openId = null;
    if (wasOpen) void tick().then(() => opener?.focus());
  }
  function onDetailKey(event: KeyboardEvent) {
    touchIdle();
    if (event.key === 'Escape') closeRoom();
  }
  // Wird der geöffnete Raum nicht mehr geliefert (Zugang geändert), wird zur Übersicht zurückgekehrt.
  $effect(() => {
    if (openId !== null && data && !openRoomData) closeRoom();
  });
  // Wechsel in die Tafel schließt das Detail.
  $effect(() => {
    if (resolved === 'tafel' && openId !== null) closeRoom();
  });
  onMount(() => () => clearIdle());
</script>

<svelte:document onvisibilitychange={() => kiosk.visibilityChanged()} />
<svelte:window bind:innerWidth={width} />

{#snippet roomCard(room: KioskRoom, snapshot: KioskState)}
  {@const busy = kiosk.pendingFor(room.id) !== undefined}
  {@const error = kiosk.errorFor(room.id)}
  {@const ov = overrideText(room.override, snapshot.timezone)}
    <section
      class="kiosk-card flex h-full flex-col gap-4 rounded-2xl border border-border bg-card p-5"
      aria-labelledby={`kiosk-room-${room.id}`}
      aria-busy={busy}
    >
      <div class="flex items-start justify-between gap-3">
        <h2 id={`kiosk-room-${room.id}`} class="text-xl font-semibold leading-tight">{room.name}</h2>
        <span class="kiosk-chip" data-heat={room.would_heat === null ? 'unknown' : room.would_heat ? 'on' : 'off'}>
          {heatText(room.would_heat)}
        </span>
      </div>

      <div>
        <p class="kiosk-temp tabular-nums" aria-label={`Ist-Temperatur ${formatTemperature(room.temperature)} Grad`}>
          {formatTemperature(room.temperature)}<span class="kiosk-unit"> °C</span>
        </p>
        <p class="text-sm text-muted-foreground">{ageText(room.measured_at, snapshot.now)}</p>
      </div>

      {#if room.sensor || room.sensor_stuck || room.emergency}
        <div class="flex flex-col gap-2">
          {#if room.sensor}<span class="kiosk-chip" data-heat="warn">Fühler: {room.sensor.label}</span>{/if}
          {#if room.sensor_stuck}
            <span class="kiosk-chip" data-heat="unknown">Messwert unverändert</span>
          {/if}
          {#if room.emergency}
            <div class="rounded-xl border border-border bg-muted p-3 text-sm">
              <p class="font-semibold">{room.emergency.headline}</p>
              <p class="text-muted-foreground">{room.emergency.detail}</p>
            </div>
          {/if}
        </div>
      {/if}

      <div class="flex items-center justify-between gap-3">
        {#if room.actions.setpoint}
          <button
            type="button"
            class="kiosk-step kiosk-btn"
            disabled={busy}
            onclick={() => kiosk.step(room, 'down')}
            aria-label={`Sollwert ${room.name} um 0,5 Grad senken`}>−</button
          >
        {/if}
        <div class="min-w-0 flex-1 text-center">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">Soll</p>
          <p class="kiosk-setpoint tabular-nums">{formatTemperature(room.setpoint.temperature)} °C</p>
        </div>
        {#if room.actions.setpoint}
          <button
            type="button"
            class="kiosk-step kiosk-btn"
            disabled={busy}
            onclick={() => kiosk.step(room, 'up')}
            aria-label={`Sollwert ${room.name} um 0,5 Grad anheben`}>+</button
          >
        {/if}
      </div>
      <p class="-mt-2 text-center text-sm text-muted-foreground">
        {room.setpoint.reason} · {room.mode_label}
      </p>

      {#if ov}<p class="rounded-xl bg-muted px-3 py-2 text-center text-sm font-medium">{ov}</p>{/if}

      {#if room.actions.boost || room.actions.cancel_override}
        <div class="mt-auto flex flex-col gap-2">
          {#if room.actions.boost}
            <button type="button" class="kiosk-touch kiosk-btn px-4" disabled={busy} onclick={() => kiosk.boost(room)}>
              Nächste Schaltung vorziehen
            </button>
          {/if}
          {#if room.actions.cancel_override}
            <button
              type="button"
              class="kiosk-touch kiosk-btn px-4"
              disabled={busy}
              onclick={() => kiosk.cancelOverride(room)}
            >
              Übersteuerung aufheben
            </button>
          {/if}
        </div>
      {/if}

      {#if error}
        <p class="kiosk-error flex items-start justify-between gap-3 rounded-xl p-3 text-sm" role="alert">
          <span>{error}</span>
          <button
            type="button"
            class="kiosk-touch shrink-0 rounded-lg px-3 font-medium underline"
            onclick={() => kiosk.dismissError(room.id)}>Schließen</button
          >
        </p>
      {/if}
    </section>
{/snippet}

<main class="kiosk min-h-dvh bg-background text-foreground">
  <header class="mx-auto flex max-w-[110rem] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight sm:text-3xl">Zuhause</h1>
      {#if data}
        <p class="text-sm text-muted-foreground">
          {clockText(data.now, data.timezone)} Uhr
          {#if kiosk.offline}· Stand der letzten Verbindung{/if}
          <!-- AGPL §13: erreichbar, aber klein und ohne Tippfläche des Bedienbereichs. -->
          · <SourceLink class="kiosk-source text-xs" />
        </p>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
    <div class="flex gap-1" role="group" aria-label="Ansicht">
      {#each KIOSK_VIEWS as v (v.id)}
        <button
          type="button"
          class="kiosk-touch rounded-xl border border-border px-3 text-sm font-medium {view === v.id ? 'bg-secondary text-secondary-foreground' : 'bg-background'}"
          aria-pressed={view === v.id}
          title={v.hint}
          onclick={() => chooseView(v.id)}>{v.label}</button
        >
      {/each}
    </div>
    <button
      type="button"
      class="kiosk-touch rounded-xl border border-border bg-background px-4 text-sm font-medium"
      onclick={nextTheme}
      aria-label={`Darstellung: ${themeLabel}. Zum Wechseln tippen.`}
    >
      {themeLabel}
    </button>
    </div>
  </header>

  <div class="sr-only" role="status" aria-live="polite">{kiosk.announcement}</div>

  {#if kiosk.status === 'loading'}
    <p class="mx-auto max-w-[110rem] px-4 py-10 text-lg text-muted-foreground sm:px-6" role="status">Lade …</p>
  {:else if kiosk.status === 'invalid'}
    <section class="mx-auto max-w-xl px-4 py-10 sm:px-6" aria-labelledby="kiosk-invalid">
      <h2 id="kiosk-invalid" class="mb-3 text-2xl font-semibold">Kiosk-Zugang nicht verfügbar</h2>
      <p class="text-lg" role="alert">{kiosk.notice ?? INVALID_TEXT}</p>
      <p class="mt-4 text-muted-foreground">
        Die Steuerung ist gesperrt. Anmelden kann man hier nicht; ein neuer Zugang kommt aus der Verwaltung.
      </p>
      <button type="button" class="kiosk-touch kiosk-btn mt-6 px-6" onclick={() => kiosk.reload()}>
        Erneut prüfen
      </button>
    </section>
  {:else if kiosk.status === 'unavailable'}
    <section class="mx-auto max-w-xl px-4 py-10 sm:px-6">
      <h2 class="mb-3 text-2xl font-semibold">Keine Daten</h2>
      <p class="text-lg" role="alert">{kiosk.notice}</p>
      <button type="button" class="kiosk-touch kiosk-btn mt-6 px-6" onclick={() => kiosk.reload()}>
        Erneut versuchen
      </button>
    </section>
  {:else if data}
    {#if kiosk.offline && kiosk.notice}
      <p
        class="mx-auto mb-4 max-w-[110rem] rounded-xl border border-border bg-muted px-4 py-3 text-sm sm:mx-6"
        role="status"
      >
        {kiosk.notice}
      </p>
    {/if}
    {#if data.rooms.length === 0}
      <p class="mx-auto max-w-[110rem] px-4 py-6 text-lg text-muted-foreground sm:px-6">
        Diesem Zugang ist kein Raum zugewiesen.
      </p>
    {:else}
      {#if resolved === 'tafel'}
        <ul class="kiosk-grid mx-auto grid max-w-[110rem] gap-4 px-4 pb-10 sm:px-6">
          {#each data.rooms as room (room.id)}
            <li>{@render roomCard(room, data)}</li>
          {/each}
        </ul>
      {:else}
        <ul class="kiosk-grid mx-auto grid max-w-[110rem] gap-3 px-4 pb-10 sm:px-6" aria-label="Räume" data-testid="kiosk-tiles">
          {#each data.rooms as room (room.id)}
            <li>
              <button
                type="button"
                class="kiosk-tile kiosk-btn flex h-full w-full flex-col items-stretch gap-2 p-4 text-left"
                data-heat={room.would_heat === null ? 'unknown' : room.would_heat ? 'on' : 'off'}
                aria-label={`${room.name}: ${formatTemperature(room.temperature)} Grad, Soll ${formatTemperature(room.setpoint.temperature)} Grad, ${heatText(room.would_heat)}. Öffnen.`}
                onclick={(e) => openRoom(room.id, e.currentTarget)}
              >
                <span class="flex items-start justify-between gap-2">
                  <span class="text-lg leading-tight font-semibold">{room.name}</span>
                  <span class="kiosk-dot" data-heat={room.would_heat === null ? 'unknown' : room.would_heat ? 'on' : 'off'} aria-hidden="true"></span>
                </span>
                <span class="kiosk-tile-temp tabular-nums">{formatTemperature(room.temperature)}<span class="kiosk-unit"> °C</span></span>
                <span class="text-sm text-muted-foreground">Soll {formatTemperature(room.setpoint.temperature)} °C · {heatText(room.would_heat)}</span>
              </button>
            </li>
          {/each}
        </ul>
        {#if openRoomData}
          <div
            class="kiosk-detail fixed inset-0 z-50 overflow-y-auto bg-background"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`kiosk-room-${openRoomData.id}`}
            tabindex="-1"
            onpointerdown={touchIdle}
            onkeydown={onDetailKey}
          >
            <div class="mx-auto flex max-w-xl flex-col gap-4 px-4 py-4">
              <div class="flex items-center justify-between gap-3">
                <button type="button" class="kiosk-touch kiosk-btn px-5" bind:this={backButton} onclick={closeRoom}>
                  Zurück zur Übersicht
                </button>
                <SourceLink class="text-xs text-muted-foreground" />
              </div>
              {@render roomCard(openRoomData, data)}
            </div>
          </div>
        {/if}
      {/if}
    {/if}
  {/if}
</main>

<style>
  .kiosk-grid {
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 21rem), 1fr));
    list-style: none;
  }
  .kiosk-touch {
    min-height: 3.5rem;
  }
  .kiosk-tile {
    min-height: 8.5rem;
    border-radius: 1rem;
    background: var(--card);
  }
  .kiosk-tile[data-heat='on'] {
    border-color: var(--ui-accent, var(--primary));
  }
  .kiosk-tile-temp {
    font-size: 2.5rem;
    font-weight: 600;
    line-height: 1;
  }
  .kiosk-dot {
    flex: none;
    width: 0.9rem;
    height: 0.9rem;
    margin-top: 0.3rem;
    border-radius: 999px;
    border: 2px solid var(--muted-foreground);
  }
  .kiosk-dot[data-heat='on'] {
    background: var(--ui-accent, var(--primary));
    border-color: var(--ui-accent, var(--primary));
  }
  .kiosk-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 0.9rem;
    background: var(--secondary);
    color: var(--secondary-foreground);
    font-weight: 600;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
  }
  .kiosk-btn:disabled {
    opacity: 0.5;
  }
  .kiosk-btn:focus-visible,
  button:focus-visible {
    outline: 3px solid var(--ring);
    outline-offset: 2px;
  }
  .kiosk-step {
    width: 4.5rem;
    height: 4.5rem;
    flex: none;
    font-size: 2.25rem;
    line-height: 1;
  }
  .kiosk-temp {
    font-size: clamp(2.75rem, 9vw, 4.25rem);
    font-weight: 600;
    line-height: 1;
  }
  .kiosk-unit {
    font-size: 0.45em;
    font-weight: 500;
    color: var(--muted-foreground);
  }
  .kiosk-setpoint {
    font-size: 1.75rem;
    font-weight: 600;
  }
  .kiosk-chip {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.2rem 0.7rem;
    font-size: 0.8rem;
    font-weight: 600;
    white-space: nowrap;
    color: var(--muted-foreground);
  }
  .kiosk-chip[data-heat='on'] {
    background: color-mix(in oklch, var(--ui-accent, var(--primary)) 16%, transparent);
    border-color: var(--ui-accent, var(--primary));
    color: var(--foreground);
  }
  .kiosk-chip[data-heat='warn'] {
    border-color: var(--destructive);
    color: var(--destructive);
  }
  .kiosk-error {
    border: 1px solid var(--destructive);
    color: var(--destructive);
  }
  @media (prefers-reduced-motion: no-preference) {
    .kiosk-btn {
      transition: background-color 120ms ease-out;
    }
  }
</style>
