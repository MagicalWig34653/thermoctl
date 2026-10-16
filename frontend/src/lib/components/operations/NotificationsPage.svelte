<script lang="ts">
  import { onMount } from 'svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { useApp } from '$lib/context';
  import { formatInstant } from '$lib/rooms/logic';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import { generalError, opsFailure, testOutcomeFromFailure, testOutcomeFromResult, type TestOutcome } from '$lib/operations/logic';
  import type { NotificationKey } from '$lib/operations/types';
  import FormCard from './FormCard.svelte';
  import LoadGate from './LoadGate.svelte';
  import OpsHelp from './OpsHelp.svelte';
  import OpsPage from './OpsPage.svelte';
  import SaveRow from './SaveRow.svelte';
  import ToggleRow from './ToggleRow.svelte';

  const { api } = useApp();
  const store = createSettingsStore(api);
  const saver = createSaver();
  const KINDS: { key: NotificationKey; label: string; description: string }[] = [
    { key: 'sensor_faults', label: 'Sensorstörungen', description: 'Ein Raum liefert keinen brauchbaren Messwert.' },
    { key: 'bridge_faults', label: 'Brückenstörungen', description: 'Die Verbindung zu den Geräten ist gestört.' },
    { key: 'command_failures', label: 'Fehlgeschlagene Befehle', description: 'Ein Befehl an ein Gerät kam nicht an.' },
    { key: 'stuck_sensor', label: 'Hängender Messwert', description: 'Ein Messwert ändert sich auffällig lange nicht.' },
    { key: 'window_alarm', label: 'Fenster-Alarm', description: 'Ein Fenster steht zu lange offen.' },
    { key: 'tenant_reports', label: 'Meldungen von Bewohnern', description: 'Eine Person meldet ein Problem.' },
  ];
  let draft = $state<Record<NotificationKey, boolean>>({
    sensor_faults: false, bridge_faults: false, command_failures: false, stuck_sensor: false, window_alarm: false, tenant_reports: false,
  });
  let ready = $state(false);
  function reset() {
    if (!store.data) return;
    for (const { key } of KINDS) draft[key] = store.data.notifications[key];
  }
  onMount(async () => {
    await store.load();
    reset();
    ready = true;
  });
  const dirty = $derived(store.data !== null && KINDS.some(({ key }) => draft[key] !== store.data!.notifications[key]));

  async function submit() {
    const result = await saver.run(
      () => store.client.saveNotifications({ ...draft }),
      () => 'Meldungseinstellungen gespeichert.',
    );
    if (result) {
      store.adopt(result);
      reset();
    }
  }

  // Test-Versand: sendet wirklich über den Webhook. Einmal je Klick, nie automatisch erneut.
  let testing = $state(false);
  let outcome = $state<TestOutcome | null>(null);
  async function sendTest() {
    if (testing) return;
    testing = true;
    outcome = null;
    try {
      outcome = testOutcomeFromResult(await store.client.testNotification());
    } catch (error) {
      outcome = testOutcomeFromFailure(opsFailure(error));
    } finally {
      testing = false;
    }
    // Der letzte Versuch steht danach in den Einstellungen; Lesen ist gefahrlos.
    try {
      store.adopt({ ...(await store.client.settings()) });
      // Entwurf nicht anfassen: ungespeicherte Schalter bleiben.
    } catch {
      /* Anzeige bleibt beim bisherigen Stand */
    }
  }
</script>

<OpsPage title="Meldungen" description="Welche Ereignisse als Meldung hinausgehen. Die Adresse des Empfängers steht in der Umgebung des Servers und wird nie angezeigt.">
  <LoadGate loaded={ready && store.data !== null} loading={store.loading || !ready} failure={store.failure} onretry={() => store.load().then(reset)}>
    {#if store.data}
      {@const n = store.data.notifications}
      <FormCard id="notify-kinds" title="Meldungsarten" description="Jede Art lässt sich einzeln ein- und ausschalten.">
        {#each KINDS as kind (kind.key)}
          <ToggleRow id={`notify-${kind.key}`} label={kind.label} description={kind.description} bind:checked={draft[kind.key]} disabled={!store.data.may_edit} />
        {/each}
        {#if store.data.may_edit}
          <SaveRow {saver} {dirty} label="Meldungen speichern" onsave={submit} onreset={reset} general={generalError(saver.failure, () => false)} />
        {/if}
      </FormCard>

      <FormCard id="notify-test" title="Empfänger und Testmeldung" description="Prüft den Weg bis zum Empfänger mit einer echten Meldung.">
        <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt class="flex items-center gap-1 text-muted-foreground">Webhook<OpsHelp term="webhook" /></dt>
          <dd class="m-0" data-testid="webhook-state">{n.webhook_configured ? 'hinterlegt' : 'nicht hinterlegt'}</dd>
          <dt class="text-muted-foreground">Letzter Versuch</dt>
          <dd class="m-0" data-testid="last-attempt">
            {#if n.last_attempt_at === null}noch keiner{:else}{formatInstant(n.last_attempt_at, store.data.timezone)} · {n.last_ok ? 'zugestellt' : 'fehlgeschlagen'}{/if}
          </dd>
          {#if n.last_error}<dt class="text-muted-foreground">Fehler</dt><dd class="m-0 break-words">{n.last_error}</dd>{/if}
        </dl>
        {#if !n.webhook_configured}
          <Notice tone="info">Ohne hinterlegten Webhook gibt es nichts zu testen. Die Adresse wird in der Umgebung des Servers gesetzt.</Notice>
        {/if}
        {#if store.data.may_edit}
          <div class="pt-2">
            <div class="flex items-center gap-1">
              <Button variant="outline" disabled={testing || !n.webhook_configured} onclick={sendTest} data-testid="send-test">{testing ? 'Wird gesendet …' : 'Testmeldung senden'}</Button>
              <OpsHelp term="test_send" />
            </div>
            {#if outcome}<div class="mt-3" data-testid="test-outcome"><Notice tone={outcome.tone}>{outcome.text}</Notice></div>{/if}
          </div>
        {/if}
      </FormCard>
    {/if}
  </LoadGate>
</OpsPage>
