// Tastenzuordnung auf der Raumseite. Der Server prüft Gerät, Raum und Recht; hier wird nur der
// Entwurf eines Formulars in den Schreibaufruf übersetzt und angezeigt, was der Server meldet.
import type { Binding, ZoneDevices } from './types';

export interface ButtonDraft {
  command: string;
  step: string;
}

export interface ButtonBody {
  device_id: number;
  action_code: string;
  command: string | null;
  step_k: string | null;
}

export const draftKey = (deviceId: number, action: string) => `${deviceId}|${action}`;

export function draftOf(binding: Binding): ButtonDraft {
  return { command: binding.command_code ?? '', step: binding.step_k ?? '' };
}

/** Leere Felder werden zu `null` (Belegung löschen bzw. keine Schrittweite); die Zahl prüft der Server. */
export function buttonBody(deviceId: number, action: string, draft: ButtonDraft): ButtonBody {
  return {
    device_id: deviceId,
    action_code: action,
    command: draft.command.trim() || null,
    step_k: draft.step.trim() || null,
  };
}

export function draftChanged(binding: Binding, draft: ButtonDraft): boolean {
  const now = draftOf(binding);
  return now.command !== draft.command || now.step !== draft.step.trim();
}

/** Geräte mit mindestens einer gesehenen Taste; ohne solche gibt es nichts zu belegen. */
export function bindableControllers(zone: Pick<ZoneDevices, 'controllers'>): ZoneDevices['controllers'] {
  return zone.controllers.filter((c) => c.bindings.length > 0);
}
