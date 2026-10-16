<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatInstant } from '$lib/rooms/logic';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import { errorAt, generalError } from '$lib/operations/logic';
  import FormCard from './FormCard.svelte';
  import LoadGate from './LoadGate.svelte';
  import OpsPage from './OpsPage.svelte';
  import OpsHelp from './OpsHelp.svelte';
  import SaveRow from './SaveRow.svelte';

  const { api } = useApp();
  const store = createSettingsStore(api);
  const saver = createSaver();
  let choice = $state(''); // leer = keine Quelle
  let ready = $state(false);

  function reset() {
    choice = store.data?.outdoor.source_device_id == null ? '' : String(store.data.outdoor.source_device_id);
  }
  onMount(async () => {
    await store.load();
    reset();
    ready = true;
  });

  const saved = $derived(store.data?.outdoor.source_device_id == null ? '' : String(store.data!.outdoor.source_device_id));
  const dirty = $derived(choice !== saved);
  const STATUS: Record<string, string> = { ok: 'Aktuell', veraltet: 'Veraltet', keine_quelle: 'Keine Quelle oder kein Messwert' };

  async function submit() {
    const result = await saver.run(
      () => store.client.saveOutdoorSource(choice === '' ? null : Number(choice)),
      () => (choice === '' ? 'Außenfühler gelöst.' : 'Außenfühler gespeichert.'),
    );
    if (result) {
      store.adopt(result);
      reset();
    } else if (saver.failure?.field) document.getElementById(`outdoor-${saver.failure.field}`)?.focus();
  }
</script>

<OpsPage title="Außenfühler" description="Welches Gerät die Außentemperatur liefert. Sie fließt in Fenster-Alarm und Notbetrieb ein.">
  <LoadGate loaded={ready && store.data !== null} loading={store.loading || !ready} failure={store.failure} onretry={() => store.load().then(reset)}>
    {#if store.data}
      {@const reading = store.data.outdoor.reading}
      <FormCard id="outdoor-reading" title="Aktuelle Außentemperatur" help="outdoor">
        <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt class="text-muted-foreground">Quelle</dt><dd class="m-0">{store.data.outdoor.source_device_name ?? 'keine'}</dd>
          <dt class="text-muted-foreground">Zustand</dt><dd class="m-0" data-testid="outdoor-status">{STATUS[reading.status] ?? reading.status}</dd>
          <dt class="text-muted-foreground">Temperatur</dt><dd class="m-0 tabular-nums">{formatCelsius(reading.temperature_c)}</dd>
          <dt class="text-muted-foreground">Gemessen</dt><dd class="m-0">{formatInstant(reading.measured_at, store.data.timezone)}</dd>
        </dl>
      </FormCard>

      <FormCard id="outdoor-source" title="Quelle wählen" description="Das Gerät muss eine Temperatur messen können; sonst lehnt der Server es ab.">
        <div>
          <div class="mb-1 flex min-h-8 items-center gap-1">
            <label class="text-sm font-medium" for="outdoor-device_id">Außenfühler</label><OpsHelp term="outdoor" />
          </div>
          <select
            id="outdoor-device_id"
            class="h-10 w-full rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive"
            bind:value={choice}
            disabled={!store.data.may_edit}
            aria-invalid={errorAt(saver.failure, 'device_id') ? 'true' : undefined}
            aria-describedby={errorAt(saver.failure, 'device_id') ? 'outdoor-device_id-error' : undefined}
          >
            <option value="">Keine Quelle</option>
            {#each store.data.outdoor.devices as device (device.id)}
              <option value={String(device.id)}>{device.name}</option>
            {/each}
          </select>
          {#if errorAt(saver.failure, 'device_id')}<p id="outdoor-device_id-error" class="mt-1 mb-0 text-sm text-destructive">{errorAt(saver.failure, 'device_id')}</p>{/if}
        </div>
        {#if store.data.may_edit}
          <SaveRow {saver} {dirty} label="Außenfühler speichern" onsave={submit} onreset={reset} general={generalError(saver.failure, (f) => f === 'device_id')} />
        {:else}
          <Notice tone="info">Du kannst die Quelle ansehen, aber nicht ändern.</Notice>
        {/if}
      </FormCard>
    {/if}
  </LoadGate>
</OpsPage>
