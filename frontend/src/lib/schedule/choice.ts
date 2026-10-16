// Welcher Raum in der Wochenplan-Ansicht zuerst gezeigt wird.
//
// Ohne eigene Wahl landet man auf dem ersten Raum, den man auch bearbeiten darf -- nicht
// blind auf dem ersten lesbaren (dort stünde dann „ansehen, aber nicht ändern", obwohl ein
// anderer Raum bearbeitbar wäre). Gibt es keinen bearbeitbaren, bleibt es der erste lesbare.
// Eine gültige eigene Wahl hat immer Vorrang. Reine Darstellung: ob geschrieben werden darf,
// entscheidet der Server beim Speichern.

export interface PlanZone {
  id: number;
  can_edit_schedule?: boolean;
}

export function scheduleZoneId(zones: readonly PlanZone[], chosen: number | null): number | null {
  const picked = zones.find((z) => z.id === chosen);
  if (picked) return picked.id;
  return (zones.find((z) => z.can_edit_schedule === true) ?? zones[0])?.id ?? null;
}
