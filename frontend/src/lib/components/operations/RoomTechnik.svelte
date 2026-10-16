<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { useApp } from '$lib/context';
  import { createOperationsClient } from '$lib/operations/client';
  import { createSaver } from '$lib/operations/saver.svelte';
  import {
    buildParametersPatch, draftFromParameters, errorAt, localParameterErrors,
    opsFailure, patchedFields, toInput, type OpsFailure, type ParametersDraft,
  } from '$lib/operations/logic';
  import type { RoomParameters } from '$lib/operations/types';
  import InheritField from './InheritField.svelte';
  import NumField from './NumField.svelte';
  import PiWarning from './PiWarning.svelte';
  import ToggleRow from './ToggleRow.svelte';

  // Raumtechnik im Raumdetail. Der Entwurf liegt im Zustand dieser Komponente, nicht im
  // Dokument: Eine eingeklappte Gruppe verliert deshalb keine ungespeicherten Werte.
  // Gesendet wird ein Teil-Update, nur geänderte Felder: fehlend = unverändert, null = erben.
  let { zoneId }: { zoneId: number } = $props();
  const { api } = useApp();
  const client = createOperationsClient(api);
  const saver = createSaver();

  let params = $state<RoomParameters | null>(null);
  let draft = $state<ParametersDraft | null>(null);
  let loadFailure = $state<OpsFailure | null>(null);
  let loading = $state(true);
  // Kein Zugriff (403) oder nicht sichtbar (404): Der Abschnitt entfällt, es ist kein Fehler.
  let unavailable = $state(false);
  let localErrors = $state<Record<string, string>>({});
  let openGroups = $state<Record<string, boolean>>({ control: true });

  async function load() {
    loading = true;
    try {
      const next = await client.parameters(zoneId);
      params = next;
      draft = draftFromParameters(next);
      loadFailure = null;
    } catch (error) {
      const failure = opsFailure(error);
      if (failure.kind === 'forbidden' || failure.kind === 'not_found') unavailable = true;
      else loadFailure = failure;
    } finally {
      loading = false;
    }
  }
  onMount(() => void load());

  const CONTROL_GROUP = ['hysteresis_k', 'min_on_seconds', 'min_off_seconds', 'sensor_timeout_seconds', 'window_resume_delay_seconds', 'temperature_offset_k'] as const;
  const GROUP_OF: Record<string, string> = {
    hysteresis_k: 'control', min_on_seconds: 'control', min_off_seconds: 'control', sensor_timeout_seconds: 'control',
    window_resume_delay_seconds: 'control', temperature_offset_k: 'control',
    solar_setback_max_k: 'sun', solar_gain_factor: 'sun',
    valve_protection_enabled: 'valve', valve_protection_interval_days: 'valve', valve_protection_duration_minutes: 'valve',
    pi_enabled: 'pi', pi_confirm: 'pi', pi_gain_per_k: 'pi', pi_integral_time_minutes: 'pi', pi_min_on_seconds: 'pi', pi_min_off_seconds: 'pi',
    window_temp_drop_detection_enabled: 'window',
    sensor_failure_enabled: 'emergency', sensor_failure_profile_id: 'emergency', sensor_failure_emergency_setpoint_c: 'emergency',
  };
  const groupOf = (field: string) => (field.startsWith('backup_offset_') ? 'offsets' : (GROUP_OF[field] ?? 'control'));
  const idOf = (field: string) => `rt-${field}`;

  const patch = $derived(params && draft ? buildParametersPatch(params, draft) : {});
  const changed = $derived(patchedFields(patch));
  const countIn = (group: string) => changed.filter((f) => (f === 'backup_offsets' ? group === 'offsets' : groupOf(f) === group)).length;
  const err = (field: string) => localErrors[field] ?? errorAt(saver.failure, field);
  const isPlaced = (field: string) => field in GROUP_OF || field.startsWith('backup_offset_');

  async function reveal(field: string) {
    openGroups[groupOf(field)] = true;
    await tick();
    document.getElementById(idOf(field))?.focus();
  }

  async function submit() {
    if (!params || !draft || changed.length === 0) return;
    const errors = localParameterErrors(params, draft);
    localErrors = errors;
    const first = Object.keys(errors)[0];
    if (first) {
      await reveal(first);
      return;
    }
    const result = await saver.run(
      () => client.saveParameters(zoneId, patch),
      () => `Raumtechnik gespeichert (${changed.length} ${changed.length === 1 ? 'Änderung' : 'Änderungen'}).`,
    );
    if (result) {
      params = result;
      draft = draftFromParameters(result);
    } else if (saver.failure?.kind === 'validation' && saver.failure.field) {
      await reveal(saver.failure.field);
    }
  }
  function discard() {
    if (params) draft = draftFromParameters(params);
    localErrors = {};
    saver.clear();
  }

  const piSwitchLocked = $derived(params !== null && !params.pi.eligible && !params.pi.enabled);
  const generalMessage = $derived(
    saver.failure && !(saver.failure.kind === 'validation' && saver.failure.field && isPlaced(saver.failure.field)) ? saver.failure.message : null,
  );
  const range = (l: { minimum: string; maximum: string } | undefined) => (l ? `Erlaubt: ${toInput(l.minimum)} bis ${toInput(l.maximum)}` : null);
</script>

{#snippet groupHead(title: string, group: string)}
  <span class="flex flex-wrap items-center gap-2">{title}
    {#if countIn(group) > 0}<span class="rounded-full bg-muted px-2 py-0.5 text-xs font-normal" data-testid={`dirty-${group}`}>{countIn(group)} ungespeichert</span>{/if}
  </span>
{/snippet}

{#if !unavailable}
  <section class="rounded-xl border bg-card p-4" aria-labelledby="rt-title" data-testid="room-technik">
    <h2 id="rt-title" class="mb-1 text-base">Raumtechnik</h2>
    {#if loading && !params}
      <p class="mb-0 text-sm text-muted-foreground" role="status">Lade …</p>
    {:else if loadFailure && !params}
      <div class="space-y-2">
        <Notice tone="error">{loadFailure.message}</Notice>
        <Button variant="outline" size="sm" onclick={load}>Erneut laden</Button>
      </div>
    {:else if params && draft}
      <p class="mb-3 text-sm text-muted-foreground">Regelwerte dieses Raums. Leere Felder erben den Wert der Anlage. Eingeklappte Gruppen behalten ungespeicherte Eingaben.</p>

      <div class="space-y-2">
        <details class="rounded-lg border" bind:open={openGroups.control}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Regelverhalten', 'control')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            {#each CONTROL_GROUP as name (name)}
              <InheritField id={idOf(name)} param={params.control[name]} bind:value={draft.control[name]} help={name === 'hysteresis_k' ? 'hysteresis' : name === 'min_on_seconds' || name === 'min_off_seconds' ? 'min_times' : undefined} error={err(name)} />
            {/each}
          </div>
        </details>

        <details class="rounded-lg border" bind:open={openGroups.sun}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Sonne', 'sun')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            <InheritField id={idOf('solar_setback_max_k')} param={params.control.solar_setback_max_k} bind:value={draft.control.solar_setback_max_k} help="solar_max" error={err('solar_setback_max_k')} />
            <NumField id={idOf('solar_gain_factor')} label="Sonneneinfluss dieses Raums" bind:value={draft.solar_gain_factor} hint="Von 0 (keine Wirkung) bis 1. Gehört nur diesem Raum und wird nicht vererbt." error={err('solar_gain_factor')} />
          </div>
        </details>

        <details class="rounded-lg border" bind:open={openGroups.valve}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Ventilschutz', 'valve')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            <ToggleRow id={idOf('valve_protection_enabled')} label="Ventilschutz" description="Bewegt Ventile in Abständen kurz." help="valve_protection" bind:checked={draft.valve.enabled} error={err('valve_protection_enabled')} />
            <NumField id={idOf('valve_protection_interval_days')} label="Abstand" unit="Tage" inputmode="numeric" bind:value={draft.valve.interval_days} error={err('valve_protection_interval_days')} />
            <NumField id={idOf('valve_protection_duration_minutes')} label="Dauer" unit="Min." inputmode="numeric" bind:value={draft.valve.duration_minutes} error={err('valve_protection_duration_minutes')} />
          </div>
        </details>

        <details class="rounded-lg border" bind:open={openGroups.pi}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('PI-Regler (Beta)', 'pi')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            <PiWarning model={params.pi_switching_model} />
            <div data-testid="pi-eligibility">
              <Notice tone={params.pi.eligible ? 'info' : 'warn'}>
                <strong>{params.pi.eligible ? 'Zulässig.' : 'Nicht zulässig.'}</strong> {params.pi.eligibility_reason}
              </Notice>
            </div>
            <ToggleRow id={idOf('pi_enabled')} label="PI-Regler verwenden" description={piSwitchLocked ? 'Kann für diesen Raum nicht eingeschaltet werden (Grund oben).' : 'Regelt stetig nach statt nur ein- und auszuschalten.'} help="pi" bind:checked={draft.pi.enabled} disabled={piSwitchLocked} error={err('pi_enabled')} />
            {#if draft.pi.enabled && !params.pi.enabled}
              <label class="flex min-h-10 items-start gap-2 text-sm">
                <input id={idOf('pi_confirm')} type="checkbox" class="mt-1 size-5" bind:checked={draft.pi.confirm} aria-invalid={err('pi_confirm') ? 'true' : undefined} />
                Ich habe verstanden, dass der PI-Regler mehr Schaltspiele erzeugt und eine falsche Einstellung die Heizung stören kann.
              </label>
              {#if err('pi_confirm')}<p class="mt-0 mb-0 text-sm text-destructive">{err('pi_confirm')}</p>{/if}
            {/if}
            <NumField id={idOf('pi_gain_per_k')} label="Verstärkung" unit="je K" bind:value={draft.pi.gain_per_k} hint={range(params.pi.limits.pi_gain_per_k)} error={err('pi_gain_per_k')} />
            <NumField id={idOf('pi_integral_time_minutes')} label="Nachstellzeit" unit="Min." inputmode="numeric" bind:value={draft.pi.integral_time_minutes} hint={range(params.pi.limits.pi_integral_time_minutes)} error={err('pi_integral_time_minutes')} />
            <NumField id={idOf('pi_min_on_seconds')} label="Mindest-Einschaltdauer" unit="s" inputmode="numeric" bind:value={draft.pi.min_on_seconds} hint={range(params.pi.limits.pi_min_on_seconds)} error={err('pi_min_on_seconds')} />
            <NumField id={idOf('pi_min_off_seconds')} label="Mindest-Ausschaltdauer" unit="s" inputmode="numeric" bind:value={draft.pi.min_off_seconds} hint={range(params.pi.limits.pi_min_off_seconds)} error={err('pi_min_off_seconds')} />
          </div>
        </details>

        <details class="rounded-lg border" bind:open={openGroups.window}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Fenster', 'window')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            <ToggleRow id={idOf('window_temp_drop_detection_enabled')} label="Fenster per Temperatursturz erkennen" description="Erkennt ein offenes Fenster auch ohne Kontakt." help="temp_drop" bind:checked={draft.window_temp_drop} error={err('window_temp_drop_detection_enabled')} />
          </div>
        </details>

        <details class="rounded-lg border" bind:open={openGroups.emergency}>
          <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Notbetrieb', 'emergency')}</summary>
          <div class="space-y-4 px-3 pt-1 pb-4">
            <ToggleRow id={idOf('sensor_failure_enabled')} label="Notbetrieb bei Sensorfehler" description="Was geschieht, wenn der Raum keinen Messwert liefert." bind:checked={draft.sensor_failure.enabled} error={err('sensor_failure_enabled')} />
            <div>
              <div class="mb-1 flex min-h-8 items-center gap-1"><label class="text-sm font-medium" for={idOf('sensor_failure_profile_id')}>Notbetriebsprofil</label></div>
              <select
                id={idOf('sensor_failure_profile_id')}
                class="h-10 w-full rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive"
                bind:value={draft.sensor_failure.profile_id}
                aria-invalid={err('sensor_failure_profile_id') ? 'true' : undefined}
                aria-describedby={`${idOf('sensor_failure_profile_id')}-note`}
              >
                <option value="">Erbt vom Standardprofil der Anlage</option>
                {#each params.sensor_failure.profiles as profile (profile.id)}<option value={String(profile.id)}>{profile.name}</option>{/each}
              </select>
              <p id={`${idOf('sensor_failure_profile_id')}-note`} class="mt-1 mb-0 text-xs text-muted-foreground">Wirksam: <strong class="text-foreground">{params.sensor_failure.effective_profile_name}</strong> · {draft.sensor_failure.profile_id === '' && params.sensor_failure.own_profile_id === null ? 'geerbt' : 'Eigenwert'}</p>
              {#if err('sensor_failure_profile_id')}<p class="mt-1 mb-0 text-sm text-destructive">{err('sensor_failure_profile_id')}</p>{/if}
            </div>
            <NumField id={idOf('sensor_failure_emergency_setpoint_c')} label="Notsollwert" unit="°C" help="emergency_setpoint" bind:value={draft.sensor_failure.emergency_setpoint_c} placeholder={toInput(params.sensor_failure.effective_emergency_setpoint_c)} hint={`Leer = erbt. Wirksam: ${toInput(params.sensor_failure.effective_emergency_setpoint_c)} °C`} error={err('sensor_failure_emergency_setpoint_c')} />
          </div>
        </details>

        {#if params.backup_offsets && params.backup_offsets.length > 0}
          <details class="rounded-lg border" bind:open={openGroups.offsets}>
            <summary class="flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">{@render groupHead('Ausgleichswerte der Ersatzquelle', 'offsets')}</summary>
            <div class="space-y-4 px-3 pt-1 pb-4">
              <p class="mb-0 text-sm text-muted-foreground">Gleicht die Abweichung einer Ersatzquelle zum Hauptfühler aus. Leer entfernt den Wert.</p>
              {#each params.backup_offsets as offset (offset.assignment_id)}
                <NumField id={idOf(`backup_offset_${offset.assignment_id}`)} label={offset.device_name} unit="K" bind:value={draft.backup_offsets[offset.assignment_id]} error={err(`backup_offset_${offset.assignment_id}`)} />
              {/each}
            </div>
          </details>
        {/if}
      </div>

      <div class="pt-4">
        <div class="flex flex-wrap items-center gap-2">
          <Button type="button" disabled={saver.busy || changed.length === 0} onclick={submit} data-testid="rt-save">{saver.busy ? 'Wird gespeichert …' : 'Raumtechnik speichern'}</Button>
          <Button type="button" variant="outline" disabled={saver.busy || changed.length === 0} onclick={discard}>Änderungen verwerfen</Button>
          {#if changed.length > 0 && !saver.busy}<span class="text-sm text-muted-foreground" data-testid="rt-dirty">{changed.length} {changed.length === 1 ? 'Änderung' : 'Änderungen'} nicht gespeichert</span>{/if}
        </div>
        <div class="mt-3 space-y-2">
          {#if generalMessage}<Notice tone="error">{generalMessage}</Notice>{/if}
          {#if (saver.failure?.kind === 'validation' && !generalMessage) || Object.keys(localErrors).length}<Notice tone="error">Bitte die markierten Angaben prüfen. Es wurde nichts gespeichert.</Notice>{/if}
          {#if saver.done}<Notice tone="success">{saver.done}</Notice>{/if}
        </div>
      </div>
    {/if}
  </section>
{/if}
