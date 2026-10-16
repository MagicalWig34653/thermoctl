<script lang="ts">
  import { onMount } from 'svelte';
  import { Plus, Trash2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import {
    buildSensorFailureBody, errorAt, generalError, sensorFailureDirty, sensorFailureDraft,
    type SensorFailureDraft,
  } from '$lib/operations/logic';
  import FormCard from './FormCard.svelte';
  import LoadGate from './LoadGate.svelte';
  import FieldGroup from './FieldGroup.svelte';
  import NumField from './NumField.svelte';
  import OpsPage from './OpsPage.svelte';
  import SaveRow from './SaveRow.svelte';

  // Notbetrieb der Anlage: Profil, Kurve, Rückkehr und Notsollwert gehen als EINE Einheit an den
  // Server, der alles vor dem ersten Schreiben prüft. Darum gibt es genau einen Speichern-Knopf.
  const { api } = useApp();
  const store = createSettingsStore(api);
  const saver = createSaver();
  let keys = 0;
  const nextKey = () => ++keys;
  let draft = $state<SensorFailureDraft | null>(null);
  let localErrors = $state<Record<string, string>>({});

  function reset() {
    if (!store.data) return;
    draft = sensorFailureDraft(store.data, nextKey);
    localErrors = {};
  }
  onMount(async () => {
    await store.load();
    reset();
  });

  const dirty = $derived(store.data !== null && draft !== null && sensorFailureDirty(store.data, draft));
  const err = (field: string) => localErrors[field] ?? errorAt(saver.failure, field);
  const SIMPLE = ['curve_points', 'fixed_on_seconds', 'fixed_off_seconds', 'recovery_seconds', 'recovery_samples', 'warm_restart_hysteresis_k', 'emergency_setpoint_c'];
  const isPlaced = (field: string) => {
    if (SIMPLE.includes(field)) return true;
    const m = /^curve_points\[(\d+)\]\./.exec(field);
    return m !== null && draft !== null && Number(m[1]) < draft.curve.length;
  };

  function addPoint() {
    draft?.curve.push({ key: nextKey(), outdoor_c: '', on_seconds: '', off_seconds: '' });
  }
  function removePoint(key: number) {
    if (draft) draft.curve = draft.curve.filter((row) => row.key !== key);
  }

  async function submit() {
    if (!draft || !store.data) return;
    const built = buildSensorFailureBody(draft);
    if (!built.ok) {
      localErrors = built.errors;
      return;
    }
    localErrors = {};
    const result = await saver.run(
      () => store.client.saveSensorFailure(built.body),
      () => 'Notbetrieb gespeichert. Profil, Kurve, Rückkehr und Notsollwert wurden zusammen übernommen.',
    );
    if (result) {
      store.adopt(result);
      reset();
    } else if (saver.failure?.field) {
      const f = saver.failure.field;
      const m = /^curve_points\[(\d+)\]\.(.+)$/.exec(f);
      const row = m && draft ? draft.curve[Number(m[1])] : undefined;
      document.getElementById(m && row ? `sf-curve-${row.key}-${m[2]}` : `sf-${f}`)?.focus();
    }
  }
</script>

<OpsPage title="Notbetrieb & Sensorfehler" description="Was die Regelung tut, wenn ein Raum keinen brauchbaren Messwert mehr liefert, und wann sie zum Normalbetrieb zurückkehrt.">
  <LoadGate loaded={store.data !== null && draft !== null} loading={store.loading} failure={store.failure} onretry={() => store.load().then(reset)}>
    {#if store.data && draft}
      <FormCard
        id="notbetrieb"
        title="Standardprofil der Anlage"
        description="Gilt für alle Räume, die kein eigenes Profil gewählt haben. Alle Angaben dieser Karte werden gemeinsam gespeichert; scheitert eine Prüfung, bleibt der bisherige Stand unverändert."
      >
        <p class="mb-0 text-sm" data-testid="sf-profile"><span class="text-muted-foreground">Profil:</span> <strong>{store.data.sensor_failure.profile_name}</strong></p>

        <FieldGroup id="sf-fixed" title="Festtakt" description="Der Ausgang taktet so, wenn keine Außentemperaturkurve eingetragen ist.">
          <NumField id="sf-fixed_on_seconds" label="Einschaltdauer" unit="s" inputmode="numeric" bind:value={draft.fixed_on_seconds} disabled={!store.data.may_edit} error={err('fixed_on_seconds')} />
          <NumField id="sf-fixed_off_seconds" label="Ausschaltdauer" unit="s" inputmode="numeric" bind:value={draft.fixed_off_seconds} disabled={!store.data.may_edit} error={err('fixed_off_seconds')} />
        </FieldGroup>

        <FieldGroup id="sf-curve" title="Außentemperaturkurve" help="emergency_curve" description="Je kälter es draußen ist, desto länger kann der Ausgang eingeschaltet sein. Ohne Punkte gilt der Festtakt.">
          {#if draft.curve.length === 0}
            <p class="mb-0 rounded-lg bg-muted/60 px-3 py-2 text-sm" data-testid="curve-empty">Keine Kurvenpunkte: Es gilt der Festtakt.</p>
          {/if}
          <ul class="m-0 list-none space-y-3 p-0">
            {#each draft.curve as row, index (row.key)}
              <li class="rounded-lg border p-3" data-testid="curve-row">
                <div class="mb-2 flex items-center justify-between gap-2">
                  <h3 class="text-sm">Punkt {index + 1}</h3>
                  {#if store.data.may_edit}
                    <Button type="button" variant="ghost" size="sm" class="min-h-10" aria-label={`Kurvenpunkt ${index + 1} entfernen`} onclick={() => removePoint(row.key)}><Trash2 /> Entfernen</Button>
                  {/if}
                </div>
                <div class="grid gap-3 sm:grid-cols-3">
                  <NumField id={`sf-curve-${row.key}-outdoor_c`} label="Außentemperatur" unit="°C" bind:value={row.outdoor_c} disabled={!store.data.may_edit} error={err(`curve_points[${index}].outdoor_c`)} />
                  <NumField id={`sf-curve-${row.key}-on_seconds`} label="An" unit="s" inputmode="numeric" bind:value={row.on_seconds} disabled={!store.data.may_edit} error={err(`curve_points[${index}].on_seconds`)} />
                  <NumField id={`sf-curve-${row.key}-off_seconds`} label="Aus" unit="s" inputmode="numeric" bind:value={row.off_seconds} disabled={!store.data.may_edit} error={err(`curve_points[${index}].off_seconds`)} />
                </div>
              </li>
            {/each}
          </ul>
          {#if err('curve_points')}<p id="sf-curve_points-error" class="mb-0 text-sm text-destructive" role="alert">{err('curve_points')}</p>{/if}
          {#if store.data.may_edit}
            <Button type="button" variant="outline" onclick={addPoint}><Plus /> Kurvenpunkt hinzufügen</Button>
          {/if}
        </FieldGroup>

        <FieldGroup id="sf-recovery" title="Rückkehr in den Normalbetrieb" description="Nach einem Sensorfehler prüft die Regelung, ob die Messung wieder verlässlich ist.">
          <NumField id="sf-recovery_seconds" label="Wartezeit vor der Rückkehr" unit="s" inputmode="numeric" help="recovery" bind:value={draft.recovery_seconds} disabled={!store.data.may_edit} error={err('recovery_seconds')} />
          <NumField id="sf-recovery_samples" label="Benötigte gute Messwerte" inputmode="numeric" bind:value={draft.recovery_samples} disabled={!store.data.may_edit} error={err('recovery_samples')} />
          <NumField id="sf-warm_restart_hysteresis_k" label="Hysterese beim Wiederanlauf" unit="K" bind:value={draft.warm_restart_hysteresis_k} disabled={!store.data.may_edit} error={err('warm_restart_hysteresis_k')} />
        </FieldGroup>

        <FieldGroup id="sf-setpoint" title="Notsollwert">
          <NumField id="sf-emergency_setpoint_c" label="Notsollwert der Anlage" unit="°C" help="emergency_setpoint" bind:value={draft.emergency_setpoint_c} disabled={!store.data.may_edit} hint="Gilt im Notbetrieb, solange der Raum keinen eigenen Notsollwert hat." error={err('emergency_setpoint_c')} />
        </FieldGroup>

        {#if store.data.may_edit}
          <SaveRow {saver} {dirty} label="Notbetrieb speichern" onsave={submit} onreset={reset} general={generalError(saver.failure, isPlaced) ?? (Object.keys(localErrors).length ? 'Bitte die markierten Angaben prüfen. Es wurde nichts gesendet.' : null)} />
        {:else}
          <Notice tone="info">Du kannst den Notbetrieb ansehen, aber nicht ändern.</Notice>
        {/if}
      </FormCard>
    {/if}
  </LoadGate>
</OpsPage>
