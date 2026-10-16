<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowRight, Sun } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { formatTemperature } from '$lib/rooms/decimal';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import {
    defaultsPayload, errorAt, fromInput, generalError, sameNumber, solarExample, toInput,
  } from '$lib/operations/logic';
  import FormCard from './FormCard.svelte';
  import LoadGate from './LoadGate.svelte';
  import NumField from './NumField.svelte';
  import OpsPage from './OpsPage.svelte';
  import SaveRow from './SaveRow.svelte';
  import ToggleRow from './ToggleRow.svelte';

  // Sonnenabsenkung: Aktivierung und Standort (eigener Endpunkt) getrennt von der Wirkung
  // (Höchstabsenkung und Vorausschau stehen bei den Regelvorgaben). Die Beispielrechnung ist reine
  // Darstellung; die Regelung rechnet auf dem Server.
  const { api, prefix } = useApp();
  const store = createSettingsStore(api);
  const locationSaver = createSaver();
  const effectSaver = createSaver();
  const MAX = 'default_solar_setback_max_k';
  const AHEAD = 'solar_setback_lookahead_hours';

  let enabled = $state(false);
  let latitude = $state('');
  let longitude = $state('');
  let maxText = $state('');
  let aheadText = $state('');
  let ready = $state(false);

  const fieldOf = (name: string) => store.data?.defaults.find((f) => f.name === name);
  function reset() {
    if (!store.data) return;
    enabled = store.data.solar.enabled;
    latitude = toInput(store.data.solar.latitude ?? '');
    longitude = toInput(store.data.solar.longitude ?? '');
    maxText = toInput(fieldOf(MAX)?.value ?? '');
    aheadText = toInput(fieldOf(AHEAD)?.value ?? '');
  }
  onMount(async () => {
    await store.load();
    reset();
    ready = true;
  });

  const locationDirty = $derived(
    store.data !== null &&
      (enabled !== store.data.solar.enabled ||
        latitude.trim() !== toInput(store.data.solar.latitude ?? '') ||
        longitude.trim() !== toInput(store.data.solar.longitude ?? '')),
  );
  const effectDirty = $derived(
    store.data !== null &&
      (!sameNumber(maxText, fieldOf(MAX)?.value ?? '') || !sameNumber(aheadText, fieldOf(AHEAD)?.value ?? '')),
  );

  async function saveLocation() {
    const result = await locationSaver.run(
      () => store.client.saveSolar({ enabled, latitude: latitude.trim() ? fromInput(latitude) : null, longitude: longitude.trim() ? fromInput(longitude) : null }),
      () => 'Aktivierung und Standort gespeichert.',
    );
    if (result) {
      store.adopt(result);
      reset();
    } else if (locationSaver.failure?.field) document.getElementById(`solar-${locationSaver.failure.field}`)?.focus();
  }
  async function saveEffect() {
    if (!store.data) return;
    const result = await effectSaver.run(
      () => {
        const body = defaultsPayload(store.data!, { [MAX]: maxText, [AHEAD]: aheadText });
        return store.client.saveDefaults(body.values, body.timezone);
      },
      () => 'Wirkung gespeichert.',
    );
    if (result) {
      store.adopt(result);
      reset();
    } else if (effectSaver.failure?.field) document.getElementById(`solar-${effectSaver.failure.field}`)?.focus();
  }

  const example = $derived(solarExample(maxText));
  const maxField = $derived(fieldOf(MAX));
  const aheadField = $derived(fieldOf(AHEAD));
  const range = (f: { minimum: string; maximum: string } | undefined) => (f ? `Erlaubt: ${toInput(f.minimum)} bis ${toInput(f.maximum)}` : null);
</script>

<OpsPage title="Sonnenabsenkung" description="Sonnenwärme mitdenken: Vor sonnigen Stunden wird der Sollwert etwas abgesenkt, damit nicht unnötig geheizt wird.">
  <LoadGate loaded={ready && store.data !== null} loading={store.loading || !ready} failure={store.failure} onretry={() => store.load().then(reset)}>
    {#if store.data}
      <FormCard id="solar-location" title="Aktivierung und Standort" description="Breiten- und Längengrad der Anlage bestimmen die Sonnenstunden. Ob die Werte zulässig sind, prüft der Server beim Speichern." help="solar">
        <ToggleRow id="solar-enabled" label="Sonnenabsenkung nutzen" description={enabled ? 'Eingeschaltet' : 'Ausgeschaltet'} bind:checked={enabled} disabled={!store.data.may_edit} error={errorAt(locationSaver.failure, 'enabled')} />
        {#if enabled && latitude.trim() === '' && longitude.trim() === ''}
          <Notice tone="warn">Die Sonnenabsenkung ist eingeschaltet, aber es ist kein Standort eingetragen.</Notice>
        {/if}
        <NumField id="solar-latitude" label="Breitengrad" unit="°" bind:value={latitude} disabled={!store.data.may_edit} hint="Von −90 (Süd) bis 90 (Nord), zum Beispiel 52,52." error={errorAt(locationSaver.failure, 'latitude')} />
        <NumField id="solar-longitude" label="Längengrad" unit="°" bind:value={longitude} disabled={!store.data.may_edit} hint="Von −180 (West) bis 180 (Ost), zum Beispiel 13,40." error={errorAt(locationSaver.failure, 'longitude')} />
        {#if store.data.may_edit}
          <SaveRow saver={locationSaver} dirty={locationDirty} label="Aktivierung speichern" onsave={saveLocation} onreset={reset} general={generalError(locationSaver.failure, (f) => ['enabled', 'latitude', 'longitude'].includes(f))} />
        {/if}
      </FormCard>

      <FormCard id="solar-effect" title="Wirkung" description="Gilt für die ganze Anlage. Einzelne Räume können in der Raumtechnik eigene Werte haben.">
        <NumField id={`solar-${MAX}`} label="Maximale Absenkung" unit="K" help="solar_max" bind:value={maxText} disabled={!store.data.may_edit} hint={range(maxField)} error={errorAt(effectSaver.failure, MAX)} />
        <NumField id={`solar-${AHEAD}`} label="Vorausschau" unit="Std." inputmode="numeric" help="solar_lookahead" bind:value={aheadText} disabled={!store.data.may_edit} hint={range(aheadField)} error={errorAt(effectSaver.failure, AHEAD)} />
        {#if store.data.may_edit}
          <SaveRow saver={effectSaver} dirty={effectDirty} label="Wirkung speichern" onsave={saveEffect} onreset={reset} general={generalError(effectSaver.failure, (f) => f === MAX || f === AHEAD)} />
        {/if}
      </FormCard>

      <section class="max-w-[820px] rounded-xl border bg-card p-5" aria-labelledby="solar-example-title" data-testid="solar-example">
        <h2 id="solar-example-title" class="mb-1 flex items-center gap-2"><Sun size={20} aria-hidden="true" /> So wirkt die Absenkung</h2>
        <p class="mb-4 text-sm font-medium">Beispiel, keine Wetterprognose.</p>
        <div class="flex flex-wrap items-center gap-4" aria-label="Beispielrechnung">
          <div>
            <div class="text-xs text-muted-foreground">Sollwert laut Wochenplan (Beispiel)</div>
            <div class="text-3xl font-semibold tabular-nums">{formatTemperature(example.plan)} <span class="text-base font-normal text-muted-foreground">°C</span></div>
          </div>
          <ArrowRight size={22} class="text-muted-foreground" aria-hidden="true" />
          <div>
            <div class="text-xs text-muted-foreground">Bei voller Absenkung (Beispiel)</div>
            <div class="text-3xl font-semibold tabular-nums" data-testid="solar-example-target">{example.target === null ? '—' : formatTemperature(example.target)} <span class="text-base font-normal text-muted-foreground">°C</span></div>
          </div>
        </div>
        <p class="mt-4 mb-0 text-sm text-muted-foreground">
          Mit {maxText.trim() || '—'} K Höchstabsenkung und {aheadText.trim() || '—'} Stunden Vorausschau. Wie stark der Sollwert tatsächlich sinkt, entscheidet die Regelung zur Laufzeit aus dem Sonnenstand, der Raumvorgabe und dem Wochenplan; der Wochenplan selbst bleibt unverändert. Der tatsächlich angewendete Wert steht beim Raum unter der Regelentscheidung.
        </p>
      </section>

      <div class="max-w-[820px]">
        <Notice tone="info">
          Sonnenprofil und Höchstabsenkung je Raum stehen in der Raumtechnik des jeweiligen Raums. <a class="underline" href={appHref(prefix, '/')}>Zu den Räumen</a>
        </Notice>
      </div>
    {/if}
  </LoadGate>
</OpsPage>
