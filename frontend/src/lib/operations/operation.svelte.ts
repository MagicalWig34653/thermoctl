// Betriebszustand (Freigaben, Rollen, Entscheidungen) im Takt gelesen. Die Zeitzone der Anlage
// kommt aus den Einstellungen; beide Aufrufe laufen nacheinander (SQLite sperrt bei Gleichzeitigkeit).
import type { Api } from '../api';
import { createResource } from '../rooms/resource.svelte';
import { createOperationsClient } from './client';
import type { Operation } from './types';

export interface OperationPage {
  operation: Operation;
  timezone: string;
}

export function createOperationResource(api: Api) {
  const client = createOperationsClient(api);
  const resource = createResource<OperationPage>(async () => {
    const operation = await client.operation();
    const settings = await client.settings();
    return { operation, timezone: settings.timezone };
  });
  return { client, resource };
}
