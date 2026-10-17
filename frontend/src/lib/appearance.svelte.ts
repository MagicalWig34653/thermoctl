// Darstellung: Hell/Dunkel/System und Akzentfarbe. Die Wahl liegt im Browser (localStorage);
// eine Speicherung je Konto auf dem Server ist noch nicht vorgesehen. Jeder Zugriff auf den
// Speicher ist abgesichert -- private Fenster und gesperrte Websitedaten dürfen nichts brechen.

import { accentTokens } from './accent';

export type Theme = 'system' | 'light' | 'dark';

export const ACCENTS: readonly { id: string; label: string; hex: string }[] = [
  { id: 'tuerkis', label: 'Türkis', hex: '#0f766e' },
  { id: 'blau', label: 'Blau', hex: '#2563eb' },
  { id: 'violett', label: 'Violett', hex: '#7c3aed' },
  { id: 'orange', label: 'Orange', hex: '#ea580c' },
  { id: 'rot', label: 'Rot', hex: '#dc2626' },
  { id: 'gruen', label: 'Grün', hex: '#15803d' },
  { id: 'gelb', label: 'Gelb', hex: '#eab308' },
  { id: 'bernstein', label: 'Bernstein', hex: '#f59e0b' },
  { id: 'limette', label: 'Limette', hex: '#84cc16' },
  { id: 'smaragd', label: 'Smaragd', hex: '#10b981' },
  { id: 'cyan', label: 'Cyan', hex: '#06b6d4' },
  { id: 'himmelblau', label: 'Himmelblau', hex: '#0ea5e9' },
  { id: 'indigo', label: 'Indigo', hex: '#4f46e5' },
  { id: 'purpur', label: 'Purpur', hex: '#a855f7' },
  { id: 'magenta', label: 'Magenta', hex: '#d946ef' },
  { id: 'pink', label: 'Pink', hex: '#ec4899' },
  { id: 'rose', label: 'Rose', hex: '#f43f5e' },
  { id: 'koralle', label: 'Koralle', hex: '#f97316' },
  { id: 'braun', label: 'Braun', hex: '#92400e' },
  { id: 'schiefer', label: 'Schiefer', hex: '#475569' },
];

const THEME_KEY = 'thermoctl-theme';
const ACCENT_KEY = 'thermoctl-accent';
export const GLOW_KEY = 'thermoctl-glow'; // alt (ein/aus), nur noch zum Übernehmen gelesen
export const GLOW_STEP_KEY = 'thermoctl-glow-step';
export const DEFAULT_ACCENT = ACCENTS[0].hex;

export function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Nicht speicherbar: die Wahl gilt dann nur für diese Sitzung.
  }
}

export function parseTheme(value: string | null): Theme {
  return value === 'light' || value === 'dark' ? value : 'system';
}

export function parseAccent(value: string | null): string {
  return value !== null && /^#[0-9a-f]{6}$/i.test(value) ? value : DEFAULT_ACCENT;
}

// Glow: sechs Stufen, 0 ist aus (Vorgabe). Das Leuchten selbst ist reines CSS (styles/glow.css),
// gesteuert über data-glow und data-glow-step am Wurzelelement; jede Stufe leuchtet stärker und
// an mehr Stellen.
export type GlowStep = 0 | 1 | 2 | 3 | 4 | 5 | 6;
export const GLOW_STEPS: { id: GlowStep; label: string }[] = [
  { id: 0, label: 'Aus' },
  { id: 1, label: 'Zart' },
  { id: 2, label: 'Fein' },
  { id: 3, label: 'Mittel' },
  { id: 4, label: 'Deutlich' },
  { id: 5, label: 'Kräftig' },
  { id: 6, label: 'Intensiv' },
];
export function parseGlowStep(value: string | null): GlowStep | null {
  return value !== null && /^[0-6]$/.test(value) ? (Number(value) as GlowStep) : null;
}
/** Gespeicherte Stufe; wer früher „Glow an“ gewählt hat, bekommt „Mittel“, sonst gilt aus. */
export function loadGlowStep(): GlowStep {
  return parseGlowStep(read(GLOW_STEP_KEY)) ?? (read(GLOW_KEY) === 'on' ? 3 : 0);
}
export function saveGlowStep(step: GlowStep) {
  write(GLOW_STEP_KEY, String(step));
}

export function createAppearance() {
  let theme = $state<Theme>(parseTheme(read(THEME_KEY)));
  let accent = $state(parseAccent(read(ACCENT_KEY)));
  let glowStep = $state<GlowStep>(loadGlowStep());
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  function apply() {
    const dark = theme === 'dark' || (theme === 'system' && media.matches);
    const root = document.documentElement;
    root.classList.toggle('dark', dark);
    root.style.colorScheme = dark ? 'dark' : 'light';
    if (glowStep > 0) {
      root.dataset.glow = 'on';
      root.dataset.glowStep = String(glowStep);
    } else {
      delete root.dataset.glow;
      delete root.dataset.glowStep;
    }
    const tokens = accentTokens(accent, dark);
    if (!tokens) return;
    const vars: Record<string, string> = {
      '--primary': tokens.primary,
      '--primary-foreground': tokens.foreground,
      '--ring': tokens.primary,
      '--ui-accent': tokens.primary,
      '--ui-accent-ink': tokens.ink,
      '--ui-accent-soft': tokens.soft,
    };
    for (const [key, value] of Object.entries(vars)) root.style.setProperty(key, value);
  }
  media.addEventListener('change', apply);
  apply();

  return {
    get theme() {
      return theme;
    },
    get accent() {
      return accent;
    },
    get glowStep() {
      return glowStep;
    },
    setGlowStep(value: GlowStep) {
      glowStep = value;
      saveGlowStep(value);
      apply();
    },
    setTheme(value: Theme) {
      theme = value;
      write(THEME_KEY, value);
      apply();
    },
    setAccent(value: string) {
      if (!/^#[0-9a-f]{6}$/i.test(value)) return;
      accent = value.toLowerCase();
      write(ACCENT_KEY, accent);
      apply();
    },
  };
}

export type Appearance = ReturnType<typeof createAppearance>;
