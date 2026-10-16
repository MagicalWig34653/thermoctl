// Aufrufe für Raum anlegen/bearbeiten/löschen, Probleme melden und das Glossar. Schreibende
// Aufrufe laufen genau einmal: kein automatisches Wiederholen, keine Warteschlange. Ein zweiter
// Versuch nach unklarem Ausgang könnte einen Raum doppelt anlegen oder eine Meldung doppelt senden.
import type { Api } from '../api';
import type {
  Deleted,
  DeleteImpact,
  Glossary,
  ReportOptions,
  ReportResult,
  RoomCreateBody,
  RoomForm,
  RoomFormOptions,
  RoomOrders,
  RoomPatch,
} from './types';

export function createZonesClient(api: Api) {
  return {
    /** Auswahllisten; mit `zoneId` zusätzlich die aktuellen Werte des Raums. */
    form: (zoneId?: number) =>
      api.get<RoomFormOptions>(zoneId === undefined ? '/ui-api/room-form' : `/ui-api/room-form?zone_id=${zoneId}`),
    orders: () => api.get<RoomOrders>('/ui-api/room-orders'),
    create: (body: RoomCreateBody) => api.post<RoomForm>('/ui-api/rooms', body),
    save: (zoneId: number, patch: RoomPatch) => api.put<RoomForm>(`/ui-api/rooms/${zoneId}`, patch),
    deleteImpact: (zoneId: number) => api.get<DeleteImpact>(`/ui-api/rooms/${zoneId}/delete-impact`),
    remove: (zoneId: number) => api.delete<Deleted>(`/ui-api/rooms/${zoneId}`),
    reportOptions: () => api.get<ReportOptions>('/ui-api/report-options'),
    report: (zoneId: number, body: { kind: string; note: string }) =>
      api.post<ReportResult>(`/ui-api/rooms/${zoneId}/report`, body),
    glossary: () => api.get<Glossary>('/ui-api/glossary'),
  };
}

export type ZonesClient = ReturnType<typeof createZonesClient>;
