# Herkunft der npm-Abhängigkeiten der Oberfläche

Quelle: `frontend/package-lock.json` (Felder `version`, `license` je Paket). Die Lizenzangabe stammt aus dem
Lockfile, nicht aus einer eigenen Prüfung der Lizenztexte. Fehlt sie dort, steht „unbekannt“ -- nichts geraten.
Gebaut wird zu statischen Dateien in `thermoctl/web/static/app/` (nicht eingecheckt, entsteht im Docker-Build
und in der CI); das Endabbild enthält kein Node.

## Direkte Abhängigkeiten

| Paket | Version | Lizenz | Art |
|---|---|---|---|
| `@lucide/svelte` | 1.57.0 | ISC | Laufzeit (ins Bundle) |
| `bits-ui` | 2.19.5 | MIT | Laufzeit (ins Bundle) |
| `clsx` | 2.1.1 | MIT | Laufzeit (ins Bundle) |
| `svelte` | 5.57.2 | MIT | Laufzeit (ins Bundle) |
| `tailwind-merge` | 3.7.0 | MIT | Laufzeit (ins Bundle) |
| `tailwind-variants` | 3.3.1 | MIT | Laufzeit (ins Bundle) |
| `tw-animate-css` | 1.4.0 | MIT | Laufzeit (ins Bundle) |
| `@internationalized/date` | 3.12.4 | Apache-2.0 | Entwicklung/Build |
| `@sveltejs/vite-plugin-svelte` | 7.3.1 | MIT | Entwicklung/Build |
| `@tailwindcss/vite` | 4.3.3 | MIT | Entwicklung/Build |
| `@types/node` | 26.6.5 | MIT | Entwicklung/Build |
| `prettier` | 3.9.10 | MIT | Entwicklung/Build |
| `prettier-plugin-svelte` | 4.1.1 | MIT | Entwicklung/Build |
| `svelte-check` | 4.7.6 | MIT | Entwicklung/Build |
| `tailwindcss` | 4.3.3 | MIT | Entwicklung/Build |
| `tsx` | 4.23.15 | MIT | Entwicklung/Build |
| `typescript` | 5.9.3 | Apache-2.0 | Entwicklung/Build |
| `vite` | 8.3.4 | MIT | Entwicklung/Build |

## Alle Pakete im Lockfile (inkl. transitiver)

| Paket | Version | Lizenz |
|---|---|---|
| `@esbuild/aix-ppc64` | 0.28.2 | MIT |
| `@esbuild/android-arm` | 0.28.2 | MIT |
| `@esbuild/android-arm64` | 0.28.2 | MIT |
| `@esbuild/android-x64` | 0.28.2 | MIT |
| `@esbuild/darwin-arm64` | 0.28.2 | MIT |
| `@esbuild/darwin-x64` | 0.28.2 | MIT |
| `@esbuild/freebsd-arm64` | 0.28.2 | MIT |
| `@esbuild/freebsd-x64` | 0.28.2 | MIT |
| `@esbuild/linux-arm` | 0.28.2 | MIT |
| `@esbuild/linux-arm64` | 0.28.2 | MIT |
| `@esbuild/linux-ia32` | 0.28.2 | MIT |
| `@esbuild/linux-loong64` | 0.28.2 | MIT |
| `@esbuild/linux-mips64el` | 0.28.2 | MIT |
| `@esbuild/linux-ppc64` | 0.28.2 | MIT |
| `@esbuild/linux-riscv64` | 0.28.2 | MIT |
| `@esbuild/linux-s390x` | 0.28.2 | MIT |
| `@esbuild/linux-x64` | 0.28.2 | MIT |
| `@esbuild/netbsd-arm64` | 0.28.2 | MIT |
| `@esbuild/netbsd-x64` | 0.28.2 | MIT |
| `@esbuild/openbsd-arm64` | 0.28.2 | MIT |
| `@esbuild/openbsd-x64` | 0.28.2 | MIT |
| `@esbuild/openharmony-arm64` | 0.28.2 | MIT |
| `@esbuild/sunos-x64` | 0.28.2 | MIT |
| `@esbuild/win32-arm64` | 0.28.2 | MIT |
| `@esbuild/win32-ia32` | 0.28.2 | MIT |
| `@esbuild/win32-x64` | 0.28.2 | MIT |
| `@floating-ui/core` | 1.8.0 | MIT |
| `@floating-ui/dom` | 1.8.0 | MIT |
| `@floating-ui/utils` | 0.2.12 | MIT |
| `@internationalized/date` | 3.12.4 | Apache-2.0 |
| `@jridgewell/gen-mapping` | 0.3.13 | MIT |
| `@jridgewell/remapping` | 2.3.5 | MIT |
| `@jridgewell/resolve-uri` | 3.1.2 | MIT |
| `@jridgewell/sourcemap-codec` | 1.6.0 | MIT |
| `@jridgewell/trace-mapping` | 0.3.31 | MIT |
| `@lucide/svelte` | 1.57.0 | ISC |
| `@oxc-project/types` | 0.154.0 | MIT |
| `@rolldown/binding-android-arm-eabi` | 1.2.14 | MIT |
| `@rolldown/binding-android-arm64` | 1.2.14 | MIT |
| `@rolldown/binding-darwin-arm64` | 1.2.14 | MIT |
| `@rolldown/binding-darwin-x64` | 1.2.14 | MIT |
| `@rolldown/binding-freebsd-x64` | 1.2.14 | MIT |
| `@rolldown/binding-linux-arm-gnueabihf` | 1.2.14 | MIT |
| `@rolldown/binding-linux-arm64-gnu` | 1.2.14 | MIT |
| `@rolldown/binding-linux-arm64-musl` | 1.2.14 | MIT |
| `@rolldown/binding-linux-ppc64-gnu` | 1.2.14 | MIT |
| `@rolldown/binding-linux-s390x-gnu` | 1.2.14 | MIT |
| `@rolldown/binding-linux-x64-gnu` | 1.2.14 | MIT |
| `@rolldown/binding-linux-x64-musl` | 1.2.14 | MIT |
| `@rolldown/binding-openharmony-arm64` | 1.2.14 | MIT |
| `@rolldown/binding-win32-arm64-msvc` | 1.2.14 | MIT |
| `@rolldown/binding-win32-x64-msvc` | 1.2.14 | MIT |
| `@rolldown/pluginutils` | 1.0.1 | MIT |
| `@sveltejs/acorn-typescript` | 1.0.13 | MIT |
| `@sveltejs/load-config` | 0.2.3 | MIT |
| `@sveltejs/vite-plugin-svelte` | 7.3.1 | MIT |
| `@swc/helpers` | 0.5.23 | Apache-2.0 |
| `@tailwindcss/node` | 4.3.3 | MIT |
| `@tailwindcss/node/node_modules/magic-string` | 0.30.21 | MIT |
| `@tailwindcss/oxide` | 4.3.3 | MIT |
| `@tailwindcss/oxide-android-arm64` | 4.3.3 | MIT |
| `@tailwindcss/oxide-darwin-arm64` | 4.3.3 | MIT |
| `@tailwindcss/oxide-darwin-x64` | 4.3.3 | MIT |
| `@tailwindcss/oxide-freebsd-x64` | 4.3.3 | MIT |
| `@tailwindcss/oxide-linux-arm-gnueabihf` | 4.3.3 | MIT |
| `@tailwindcss/oxide-linux-arm64-gnu` | 4.3.3 | MIT |
| `@tailwindcss/oxide-linux-arm64-musl` | 4.3.3 | MIT |
| `@tailwindcss/oxide-linux-x64-gnu` | 4.3.3 | MIT |
| `@tailwindcss/oxide-linux-x64-musl` | 4.3.3 | MIT |
| `@tailwindcss/oxide-wasm32-wasi` | 4.3.3 | MIT |
| `@tailwindcss/oxide-win32-arm64-msvc` | 4.3.3 | MIT |
| `@tailwindcss/oxide-win32-x64-msvc` | 4.3.3 | MIT |
| `@tailwindcss/vite` | 4.3.3 | MIT |
| `@types/estree` | 1.0.9 | MIT |
| `@types/node` | 26.6.5 | MIT |
| `acorn` | 8.19.0 | MIT |
| `aria-query` | 5.3.1 | Apache-2.0 |
| `axobject-query` | 4.1.0 | Apache-2.0 |
| `bits-ui` | 2.19.5 | MIT |
| `chokidar` | 4.0.3 | MIT |
| `clsx` | 2.1.1 | MIT |
| `deepmerge` | 4.3.1 | MIT |
| `dequal` | 2.0.3 | MIT |
| `detect-libc` | 2.1.2 | Apache-2.0 |
| `devalue` | 5.9.4 | MIT |
| `enhanced-resolve` | 5.26.0 | MIT |
| `esbuild` | 0.28.2 | MIT |
| `esm-env` | 1.2.2 | MIT |
| `esrap` | 2.4.0 | MIT |
| `fdir` | 6.5.0 | MIT |
| `fsevents` | 2.3.3 | MIT |
| `graceful-fs` | 4.2.11 | ISC |
| `inline-style-parser` | 0.2.7 | MIT |
| `is-reference` | 3.0.3 | MIT |
| `jiti` | 2.7.0 | MIT |
| `lightningcss` | 1.32.0 | MPL-2.0 |
| `lightningcss-android-arm64` | 1.32.0 | MPL-2.0 |
| `lightningcss-darwin-arm64` | 1.32.0 | MPL-2.0 |
| `lightningcss-darwin-x64` | 1.32.0 | MPL-2.0 |
| `lightningcss-freebsd-x64` | 1.32.0 | MPL-2.0 |
| `lightningcss-linux-arm-gnueabihf` | 1.32.0 | MPL-2.0 |
| `lightningcss-linux-arm64-gnu` | 1.32.0 | MPL-2.0 |
| `lightningcss-linux-arm64-musl` | 1.32.0 | MPL-2.0 |
| `lightningcss-linux-x64-gnu` | 1.32.0 | MPL-2.0 |
| `lightningcss-linux-x64-musl` | 1.32.0 | MPL-2.0 |
| `lightningcss-win32-arm64-msvc` | 1.32.0 | MPL-2.0 |
| `lightningcss-win32-x64-msvc` | 1.32.0 | MPL-2.0 |
| `locate-character` | 3.0.0 | MIT |
| `lz-string` | 1.5.0 | MIT |
| `magic-string` | 1.4.3 | MIT |
| `mri` | 1.2.0 | MIT |
| `nanoid` | 3.3.20 | MIT |
| `obug` | 2.2.1 | MIT |
| `picocolors` | 1.1.1 | ISC |
| `picomatch` | 4.0.7 | MIT |
| `postcss` | 8.5.30 | MIT |
| `prettier` | 3.9.10 | MIT |
| `prettier-plugin-svelte` | 4.1.1 | MIT |
| `readdirp` | 4.1.2 | MIT |
| `rolldown` | 1.2.14 | MIT |
| `runed` | 0.35.1 | MIT |
| `sade` | 1.8.1 | MIT |
| `source-map-js` | 1.2.2 | BSD-3-Clause |
| `style-to-object` | 1.0.14 | MIT |
| `svelte` | 5.57.2 | MIT |
| `svelte-check` | 4.7.6 | MIT |
| `svelte-toolbelt` | 0.10.6 | unbekannt |
| `svelte/node_modules/magic-string` | 0.30.21 | MIT |
| `tabbable` | 6.5.0 | MIT |
| `tailwind-merge` | 3.7.0 | MIT |
| `tailwind-variants` | 3.3.1 | MIT |
| `tailwindcss` | 4.3.3 | MIT |
| `tapable` | 2.3.3 | MIT |
| `tinyglobby` | 0.2.17 | MIT |
| `tslib` | 2.8.1 | 0BSD |
| `tsx` | 4.23.15 | MIT |
| `tw-animate-css` | 1.4.0 | MIT |
| `typescript` | 5.9.3 | Apache-2.0 |
| `undici-types` | 8.9.0 | MIT |
| `vite` | 8.3.4 | MIT |
| `vite/node_modules/lightningcss` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-android-arm64` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-darwin-arm64` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-darwin-x64` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-freebsd-x64` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-linux-arm-gnueabihf` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-linux-arm64-gnu` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-linux-arm64-musl` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-linux-x64-gnu` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-linux-x64-musl` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-win32-arm64-msvc` | 1.33.0 | MPL-2.0 |
| `vite/node_modules/lightningcss-win32-x64-msvc` | 1.33.0 | MPL-2.0 |
| `vitefu` | 1.1.3 | MIT |
| `zimmerframe` | 1.1.5 | MIT |

Hinweise:
- `svelte-toolbelt` 0.10.6 trägt im Lockfile keine Lizenzangabe; die `LICENSE`-Datei im Paketverzeichnis (node_modules der Durchstich-Fassung) nennt „MIT License“. Lizenz laut Lockfile bleibt unbekannt; die MIT-Angabe stammt aus der Datei, nicht aus dem Lockfile.
- 24 Pakete stehen unter MPL-2.0 (Dateiebenen-Copyleft); vor einer Veröffentlichung prüfen, ob das für die Auslieferung relevant ist.

## Pflege

Bei jeder Änderung an `package-lock.json` diese Datei nachziehen (Version und Lizenz aus dem Lockfile).
