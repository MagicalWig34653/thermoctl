// „Letzter Wert“ eines lesbaren Merkmals am Bediengerät, mit Alter und Zeitpunkt in der Zeit
// der Anlage (nicht der des Browsers). Der Server liefert Wert, Zeitpunkt und Alter; hier wird
// nur formuliert.

import { formatAge, formatInstant } from '../rooms/logic';
import type { ControllerProperty } from './types';

export function lastValueLine(
  property: Pick<ControllerProperty, 'last_value' | 'last_value_at' | 'last_value_age_seconds' | 'unit' | 'value_type'>,
  timeZone: string,
  now: Date = new Date(),
): string {
  if (property.last_value === null) return 'Noch kein Wert empfangen';
  const shown = property.value_type === 'numeric' ? property.last_value.replace('.', ',') : property.last_value;
  const value = property.unit ? `${shown} ${property.unit}` : shown;
  if (property.last_value_at === null) return value;
  return `${value} · ${formatAge(property.last_value_age_seconds)} (${formatInstant(property.last_value_at, timeZone, now)})`;
}
