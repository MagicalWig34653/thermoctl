// Symbole je Bereich. Getrennt von `navigation.ts`, damit die Abbildung ohne Svelte-Abhängigkeit
// (und damit unter node:test) geladen werden kann.
import type { Component } from 'svelte';
import {
  CalendarDays,
  ChartNoAxesCombined,
  House,
  Network,
  Settings2,
  Thermometer,
  Users,
} from '@lucide/svelte';

export const AREA_ICONS: Record<string, Component<any>> = {
  uebersicht: House,
  zeit: CalendarDays,
  auswertung: ChartNoAxesCombined,
  geraete: Network,
  heizung: Thermometer,
  system: Settings2,
  personen: Users,
};
