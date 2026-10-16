// Einstellungen der Anlage, einmal beim Öffnen gelesen. Bewusst ohne Nachladen im Takt:
// Ein Nachladen würde Eingaben, die noch nicht gespeichert sind, unter den Händen ändern.
import { createOperationsClient } from './client';
import { opsFailure, type OpsFailure } from './logic';
import type { Settings } from './types';
import type { Api } from '../api';

export function createSettingsStore(api: Api) {
  const client = createOperationsClient(api);
  let data = $state<Settings | null>(null);
  let failure = $state<OpsFailure | null>(null);
  let loading = $state(true);
  return {
    client,
    get data() {
      return data;
    },
    get failure() {
      return failure;
    },
    get loading() {
      return loading;
    },
    async load() {
      loading = true;
      try {
        data = await client.settings();
        failure = null;
      } catch (error) {
        failure = opsFailure(error);
      } finally {
        loading = false;
      }
    },
    /** Antwort eines Schreibaufrufs übernehmen: Sie ist der neue gespeicherte Stand. */
    adopt(next: Settings) {
      data = next;
      failure = null;
    },
  };
}

export type SettingsStore = ReturnType<typeof createSettingsStore>;
