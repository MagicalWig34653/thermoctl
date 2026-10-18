<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import {
    defaultsPayload, dirtyFields, draftFromFields, errorAt, generalError,
  } from '$lib/operations/logic';
  import type { NumberField } from '$lib/operations/types';
  import FormCard from './FormCard.svelte';
  import LoadGate from './LoadGate.svelte';
  import FieldGroup from './FieldGroup.svelte';
  import NumField from './NumField.svelte';
  import OpsPage from './OpsPage.svelte';
  import SaveRow from './SaveRow.svelte';

  // Regelvorgaben der Anlage. Der Server verlangt alle Felder und die Zeitzone in einem Aufruf;
  // die beiden Sonnenfelder gehören zur Sonnenabsenkung und werden dort bearbeitet, hier aber
  // unverändert mitgeschickt.
  const { api } = useApp();
  const store = createSettingsStore(api);
  const saver = createSaver();
  let draft = $state<Record<string, string>>({});
  let timezone = $state('');
  let ready = $state(false);

  const SOLAR = ['default_solar_setback_max_k', 'solar_setback_lookahead_hours'];
  const GROUPS: { title: string; help?: string; names: string[] }[] = [
    { title: 'Schaltverhalten', help: 'hysteresis', names: ['default_hysteresis_k', 'default_min_on_seconds', 'default_min_off_seconds'] },
    { title: 'Sensoren und Fenster', names: ['default_sensor_timeout_seconds', 'default_window_resume_delay_seconds', 'stuck_reading_hours'] },
    { title: 'Takt', names: ['polling_interval_seconds', 'shadow_interval_seconds'] },
    { title: 'Aufbewahrung und Sitzung', names: ['measurement_retention_days', 'shadow_decision_retention_days', 'command_log_retention_days', 'command_log_max_mb', 'session_lifetime_seconds'] },
    { title: 'Relais', names: ['assumed_relay_lifetime_operations'] },
  ];

  function reset() {
    if (!store.data) return;
    draft = draftFromFields(store.data.defaults);
    timezone = store.data.timezone;
  }
  onMount(async () => {
    await store.load();
    reset();
    ready = true;
  });

  const fields = $derived(store.data?.defaults ?? []);
  const byName = $derived(new Map(fields.map((f) => [f.name, f])));
  const grouped = $derived(new Set([...GROUPS.flatMap((g) => g.names), ...SOLAR]));
  const rest = $derived(fields.filter((f) => !grouped.has(f.name)));
  const shown = $derived(fields.filter((f) => !SOLAR.includes(f.name)));
  const dirty = $derived(
    dirtyFields(shown, draft).length > 0 || (store.data !== null && timezone.trim() !== store.data.timezone),
  );
  const placed = $derived(new Set([...shown.map((f) => f.name), 'timezone']));

  async function submit() {
    if (!store.data) return;
    const result = await saver.run(
      () => {
        const body = defaultsPayload(store.data!, draft, timezone);
        return store.client.saveDefaults(body.values, body.timezone);
      },
      () => 'Regelvorgaben gespeichert.',
    );
    if (result) {
      store.adopt(result);
      reset();
    } else if (saver.failure?.field) {
      document.getElementById(`rv-${saver.failure.field}`)?.focus();
    }
  }

  const hint = (f: NumberField) => `Erlaubt: ${f.minimum.replace('.', ',')} bis ${f.maximum.replace('.', ',')}`;
  const helpFor = (name: string) =>
    name === 'default_hysteresis_k' ? 'hysteresis' : name === 'default_min_on_seconds' || name === 'default_min_off_seconds' ? 'min_times' : undefined;
</script>

<OpsPage title="Regelvorgaben" description="Vorgaben für die ganze Anlage. Räume übernehmen sie, solange sie keinen eigenen Wert haben.">
  <LoadGate loaded={ready && store.data !== null} loading={store.loading || !ready} failure={store.failure} onretry={() => store.load().then(reset)}>
    {#if store.data}
      <FormCard id="regelvorgaben" title="Anlagenvorgaben" description="Eine Änderung gilt für alle Räume ohne eigenen Wert. Die Sonnenabsenkung hat eine eigene Seite.">
        {#each GROUPS as group, gi (group.title)}
          {@const members = group.names.flatMap((n) => (byName.get(n) ? [byName.get(n)!] : []))}
          {#if members.length}
            <FieldGroup id={`rv-g${gi}`} title={group.title}>
              {#each members as field (field.name)}
                <NumField
                  id={`rv-${field.name}`}
                  label={field.label}
                  bind:value={draft[field.name]}
                  inputmode={field.integer ? 'numeric' : 'decimal'}
                  disabled={!store.data.may_edit}
                  help={helpFor(field.name)}
                  hint={hint(field)}
                  error={errorAt(saver.failure, field.name)}
                />
              {/each}
            </FieldGroup>
          {/if}
        {/each}
        {#if rest.length}
          <FieldGroup id="rv-rest" title="Weitere Vorgaben">
            {#each rest as field (field.name)}
              <NumField id={`rv-${field.name}`} label={field.label} bind:value={draft[field.name]} inputmode={field.integer ? 'numeric' : 'decimal'} disabled={!store.data.may_edit} hint={hint(field)} error={errorAt(saver.failure, field.name)} />
            {/each}
          </FieldGroup>
        {/if}
        <FieldGroup id="rv-zone" title="Zeitzone">
          <NumField id="rv-timezone" label="Zeitzone der Anlage" bind:value={timezone} inputmode="text" disabled={!store.data.may_edit} hint="Name der Zeitzone, zum Beispiel Europe/Berlin. Wochenpläne und Anzeigen richten sich danach." error={errorAt(saver.failure, 'timezone')} />
        </FieldGroup>
        {#if store.data.may_edit}
          <SaveRow {saver} {dirty} label="Regelvorgaben speichern" onsave={submit} onreset={reset} general={generalError(saver.failure, (f) => placed.has(f))} />
        {:else}
          <p class="text-sm text-muted-foreground">Du kannst die Vorgaben ansehen, aber nicht ändern.</p>
        {/if}
      </FormCard>
    {/if}
  </LoadGate>
</OpsPage>
