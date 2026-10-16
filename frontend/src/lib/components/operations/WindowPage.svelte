<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { createSettingsStore } from '$lib/operations/settings.svelte';
  import LoadGate from './LoadGate.svelte';
  import OpsPage from './OpsPage.svelte';
  import ThresholdCard from './ThresholdCard.svelte';
  import Notice from '$lib/components/rooms/Notice.svelte';

  const { api } = useApp();
  const store = createSettingsStore(api);
  onMount(() => void store.load());
</script>

<OpsPage title="Fenster & Alarm" description="Wann ein offenes Fenster als vergessen gilt und wie ein Fenster ohne Kontakt erkannt wird.">
  <LoadGate loaded={store.data !== null} loading={store.loading} failure={store.failure} onretry={() => store.load()}>
    {#if store.data}
      <ThresholdCard
        id="window-alarm"
        title="Fenster-Alarm"
        description="Ein Fenster, das länger offen steht, wird gemeldet, sobald die Außentemperatur unter der Schwelle liegt. Ob die Meldung hinausgeht, steht unter Meldungen."
        help="window_alarm"
        fields={store.data.window_alarm}
        pick={(s) => s.window_alarm}
        canEdit={store.data.may_edit}
        save={(body) => store.client.saveWindowAlarm(body)}
        onsaved={(next) => store.adopt(next)}
      />
      <ThresholdCard
        id="window-temp-drop"
        title="Erkennung per Temperatursturz"
        description="Erkennt ein geöffnetes Fenster am steilen Temperaturabfall, auch ohne Fensterkontakt. Eingeschaltet wird die Erkennung je Raum in der Raumtechnik."
        help="temp_drop"
        fields={store.data.window_temp_drop}
        pick={(s) => s.window_temp_drop}
        canEdit={store.data.may_edit}
        save={(body) => store.client.saveWindowTempDrop(body)}
        onsaved={(next) => store.adopt(next)}
      />
      <div class="max-w-[820px]">
        <Notice tone="info">Die Schwellen gelten für die ganze Anlage. Jeder Raum kann die Erkennung per Temperatursturz für sich ein- oder ausschalten.</Notice>
      </div>
    {/if}
  </LoadGate>
</OpsPage>
