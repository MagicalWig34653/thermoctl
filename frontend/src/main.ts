import { mount } from 'svelte';
import { runtimePrefix } from '$lib/api';
import { isKioskPath, isSetupPath, pageKeyOf } from '$lib/paths';
import { preloadPage } from '$lib/views/pages';
import { reloadOnce } from '$lib/reload';
import './app.css';

// Die Kioskansicht hat einen eigenen Principal (Kiosk-Cookie), eine eigene Schnittstelle und
// keine Anmeldung. Sie wird deshalb VOR allem gewählt, was die Hauptanwendung beim Start
// tut (Bootstrap, Sitzungsprüfung, Anmeldeseite) -- und lädt diesen Code nicht einmal.
const target = document.getElementById('app')!;
const failed = (what: string) => (error: unknown) => {
  console.error(`${what} konnte nicht geladen werden`, error);
  if (!reloadOnce()) {
    target.textContent = 'Die Anwendung konnte nicht geladen werden. Bitte die Seite neu laden.';
  }
};
if (isKioskPath(window.location.pathname, runtimePrefix())) {
  void import('$lib/kiosk/KioskApp.svelte').then(({ default: KioskApp }) => mount(KioskApp, { target })).catch(failed('Kiosk'));
} else if (isSetupPath(window.location.pathname, runtimePrefix())) {
  // Ersteinrichtung: ohne Hülle, ohne Bootstrap (es gibt noch keine Sitzung und keinen Benutzer).
  void import('$lib/setup/SetupApp.svelte').then(({ default: SetupApp }) => mount(SetupApp, { target })).catch(failed('Einrichtung'));
} else {
  // Den Code der angefragten Seite schon jetzt holen, parallel zu App-Code und Bootstrap. Sonst käme
  // er erst nach beiden und kostete eine weitere Runde Latenz (Wasserfall beim Direktaufruf).
  const key = pageKeyOf(window.location.pathname, runtimePrefix());
  if (key) preloadPage(key);
  void import('./App.svelte').then(({ default: App }) => mount(App, { target })).catch(failed('Anwendung'));
}
