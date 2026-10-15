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
  build: {
    outDir: fileURLToPath(new URL('../thermoctl/web/static/app', import.meta.url)),
    // Das Ziel liegt außerhalb des Vite-Wurzelverzeichnisses, Löschen muss ausdrücklich erlaubt werden.
    emptyOutDir: true,
  },
});
