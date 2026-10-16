import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

// Die Ausgabe landet im Python-Paket und wird von dort ausgeliefert
// (thermoctl/web/spa_views.py). `base: './'` hält die Asset-Pfade relativ: der echte
// Pfadpräfix (Home-Assistant-Ingress) ist zur Build-Zeit unbekannt und wird erst zur
// Laufzeit vom Server eingesetzt.
export default defineConfig({
  base: './',
  plugins: [svelte(), tailwindcss()],
  resolve: {
    alias: { $lib: fileURLToPath(new URL('./src/lib', import.meta.url)) },
  },
  server: {
    // Entwicklungsserver: API, Assets und die alten HTML-Seiten kommen vom lokal laufenden
    // Backend. Adresse über THERMOCTL_DEV_BACKEND änderbar (nicht fest verdrahtet).
    proxy: Object.fromEntries(
      ['/ui-api', '/kiosk-api', '/static', '/login', '/passkey'].map((path) => [
        path,
        { target: process.env.THERMOCTL_DEV_BACKEND ?? 'http://127.0.0.1:8000', changeOrigin: false },
      ]),
    ),
  },
  build: {
    outDir: fileURLToPath(new URL('../thermoctl/web/static/app', import.meta.url)),
    // Das Ziel liegt außerhalb des Vite-Wurzelverzeichnisses, Löschen muss ausdrücklich erlaubt werden.
    emptyOutDir: true,
  },
});
