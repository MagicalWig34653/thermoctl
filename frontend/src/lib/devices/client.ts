// Aufrufe der Geräte-Schnittstelle. Schreibende Aufrufe laufen genau einmal: Es gibt kein
// automatisches Wiederholen, weil ein zweiter Versuch nach unklarem Ausgang etwas doppelt
// auslösen könnte (Zuordnung, Betriebsart).
import type { Api } from '../api';
import { buildQuery } from '../history/reports';
import type {
  CheckVerdict, CommandLog, ConnectResult, Controllers, DeviceMap, FlowZone, LayoutBody, Plant, ServerLayout, Survey,
  ZoneDevices,
} from './types';

const LAYOUT = '/ui-api/devices/map/layout';

export function createDevicesClient(api: Api) {
  const zone = (id: number) => `/ui-api/zones/${id}/devices`;
  return {
    survey: () => api.get<Survey>('/ui-api/devices'),
    map: () => api.get<DeviceMap>('/ui-api/devices/map'),
    layout: () => api.get<ServerLayout>(LAYOUT),
    saveLayout: (body: LayoutBody) => api.put<ServerLayout>(LAYOUT, body),
    resetLayout: () => api.delete<ServerLayout>(LAYOUT),
    saveDefaultLayout: (body: LayoutBody) => api.put<ServerLayout>(`${LAYOUT}/default`, body),
    removeDefaultLayout: () => api.delete<ServerLayout>(`${LAYOUT}/default`),
    check: (from: string, to: string) =>
      api.get<CheckVerdict>(`/ui-api/devices/map/check${buildQuery({ from_id: from, to_id: to })}`),
    connect: (from: string, to: string) =>
      api.post<ConnectResult>('/ui-api/devices/map/connect', { from_id: from, to_id: to }),
    disconnect: (connectionId: string) =>
      api.post<ConnectResult>('/ui-api/devices/map/disconnect', { connection_id: connectionId }),
    zoneDevices: (id: number) => api.get<ZoneDevices>(zone(id)),
    assign: (id: number, deviceId: number, roleCode: string) =>
      api.post<ZoneDevices>(`${zone(id)}/assign`, { device_id: deviceId, role_code: roleCode }),
    detach: (id: number, assignmentId: number) =>
      api.post<ZoneDevices>(`${zone(id)}/detach`, { assignment_id: assignmentId }),
    regulation: (id: number, assignmentId: number, mode: string) =>
      api.post<ZoneDevices>(`${zone(id)}/regulation`, { assignment_id: assignmentId, thermostat_mode: mode }),
    source: (id: number, deviceId: number | null) =>
      api.post<ZoneDevices>(`${zone(id)}/source`, { device_id: deviceId }),
    swap: (id: number, oldId: number, newId: number) =>
      api.post<ZoneDevices>(`${zone(id)}/swap`, { old_device_id: oldId, new_device_id: newId }),
    plant: () => api.get<Plant>('/ui-api/plant'),
    zoneButton: (id: number, body: { device_id: number; action_code: string; command: string | null; step_k: string | null }) =>
      api.post<ZoneDevices>(`${zone(id)}/button`, body),
    zoneDataFlow: (id: number) => api.get<FlowZone>(`/ui-api/zones/${id}/data-flow`),
    dataFlow: () => api.get<{ zones: FlowZone[] }>('/ui-api/zones/data-flow'),
    controllers: () => api.get<Controllers>('/ui-api/controllers'),
    channel: (body: {
      device_id: number; property_name: string; direction: string; kind: string;
      zone_id: number | null; source_device_id: number | null; fixed_text: string | null; fixed_number: string | null;
    }) => api.post<Controllers>('/ui-api/controllers/channel', body),
    button: (body: { device_id: number; action_code: string; command: string | null; step_k: string | null }) =>
      api.post<Controllers>('/ui-api/controllers/button', body),
    commands: (query: Record<string, string | number | boolean>) =>
      api.get<CommandLog>(`/ui-api/device-commands${buildQuery(query)}`),
  };
}

export type DevicesClient = ReturnType<typeof createDevicesClient>;
