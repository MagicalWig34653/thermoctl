import { mount } from 'svelte';
import { runtimePrefix } from '$lib/api';
import { isKioskPath } from '$lib/paths';
import './app.css';

// Die Kioskansicht hat einen eigenen Principal (Kiosk-Cookie), eine eigene Schnittstelle und
// keine Anmeldung. Sie wird deshalb VOR allem gewählt, was die Hauptanwendung beim Start
// tut (Bootstrap, Sitzungsprüfung, Anmeldeseite) -- und lädt diesen Code nicht einmal.
const target = document.getElementById('app')!;
if (isKioskPath(window.location.pathname, runtimePrefix())) {
  void import('$lib/kiosk/KioskApp.svelte').then(({ default: KioskApp }) => mount(KioskApp, { target }));
} else {
  void import('./App.svelte').then(({ default: App }) => mount(App, { target }));
}
