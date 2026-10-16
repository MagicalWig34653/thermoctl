// Aufrufe der Regelungs-, Betriebs- und Einstellungsschnittstelle. Schreibende Aufrufe laufen
// genau einmal: kein automatisches Wiederholen, keine Warteschlange. Ein zweiter Versuch nach
// unklarem Ausgang könnte eine Freigabe oder einen Regelparameter doppelt setzen.
import type { Api } from '../api';
import type {
  ArmedResult, Interfaces, NotificationKey, NotificationTest, Operation, ParametersPatch,
  RoomParameters, Settings,
} from './types';

export interface ThresholdBody {
  values: Record<string, string>;
}

export interface SensorFailureBody {
  fixed_on_seconds: string;
  fixed_off_seconds: string;
  recovery_seconds: string;
  recovery_samples: string;
  warm_restart_hysteresis_k: string;
  emergency_setpoint_c: string;
  curve_points: { outdoor_c: string; on_seconds: number; off_seconds: number }[];
}

export function createOperationsClient(api: Api) {
  return {
    parameters: (zoneId: number) => api.get<RoomParameters>(`/ui-api/rooms/${zoneId}/parameters`),
    saveParameters: (zoneId: number, patch: ParametersPatch) =>
      api.put<RoomParameters>(`/ui-api/rooms/${zoneId}/parameters`, patch),
    operation: () => api.get<Operation>('/ui-api/operation'),
    setArmed: (armed: boolean, reason: string) =>
      api.put<ArmedResult>('/ui-api/operation/armed', { armed, reason }),
    settings: () => api.get<Settings>('/ui-api/settings'),
    saveDefaults: (values: Record<string, string>, timezone: string) =>
      api.put<Settings>('/ui-api/settings/defaults', { values, timezone }),
    saveSolar: (body: { enabled: boolean; latitude: string | null; longitude: string | null }) =>
      api.put<Settings>('/ui-api/settings/solar-location', body),
    saveWindowAlarm: (body: ThresholdBody) => api.put<Settings>('/ui-api/settings/window-alarm', body),
    saveWindowTempDrop: (body: ThresholdBody) => api.put<Settings>('/ui-api/settings/window-temp-drop', body),
    saveSensorFailure: (body: SensorFailureBody) => api.put<Settings>('/ui-api/settings/sensor-failure', body),
    saveOutdoorSource: (deviceId: number | null) =>
      api.put<Settings>('/ui-api/settings/outdoor-source', { device_id: deviceId }),
    saveNotifications: (body: Record<NotificationKey, boolean>) =>
      api.put<Settings>('/ui-api/settings/notifications', body),
    testNotification: () => api.post<NotificationTest>('/ui-api/settings/notifications/test'),
    interfaces: () => api.get<Interfaces>('/ui-api/interfaces'),
  };
}

export type OperationsClient = ReturnType<typeof createOperationsClient>;
