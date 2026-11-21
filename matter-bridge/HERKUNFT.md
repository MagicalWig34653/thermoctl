# Herkunft der npm-Abhängigkeiten der Matter-Bridge

Quelle: `matter-bridge/package-lock.json` (Felder `version`, `license` je Paket). Die Lizenzangabe stammt aus dem
Lockfile, nicht aus einer eigenen Prüfung der Lizenztexte; fehlt sie dort, steht „unbekannt“ -- nichts geraten.
Alle direkten Abhängigkeiten sind exakt gepinnt (keine Bereiche); `npm ci` installiert genau das Lockfile.
Die Bridge ist ein eigener, optionaler Dienst (eigenes Abbild, siehe `README.md`); das thermoctl-Abbild enthält
davon nichts. `npm audit` (Stand 2026-11-21, 63 Pakete): 0 Schwachstellen, auch mit `--omit=dev`.

Die Bridge ist **nicht zertifiziert** und trägt die Matter-Test-Hersteller-ID 0xFFF1. Matter.js ist die
Open-Source-Implementierung des Matter-Standards (Apache-2.0) und kein Zertifizierungsnachweis.

## Direkte Abhängigkeiten

| Paket | Version | Lizenz | Art |
|---|---|---|---|
| `@matter/main` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/nodejs` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/protocol` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/types` | 0.18.0 | Apache-2.0 | Laufzeit |
| `mqtt` | 5.16.1 | MIT | Laufzeit |
| `aedes` | 1.2.0 | MIT | nur Tests (nicht im Abbild) |

## Alle Pakete im Lockfile (inkl. transitiver)

| Paket | Version | Lizenz | Art |
|---|---|---|---|
| `@babel/runtime` | 7.29.15 | MIT | Laufzeit |
| `@matter/general` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/main` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/model` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/node` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/nodejs` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/protocol` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@matter/types` | 0.18.0 | Apache-2.0 | Laufzeit |
| `@noble/ciphers` | 2.4.0 | MIT | Laufzeit |
| `@noble/curves` | 2.4.0 | MIT | Laufzeit |
| `@noble/hashes` | 2.4.0 | MIT | Laufzeit |
| `@noble/post-quantum` | 0.7.1 | MIT | Laufzeit |
| `@types/node` | 26.6.6 | MIT | Laufzeit |
| `@types/readable-stream` | 4.0.25 | MIT | Laufzeit |
| `@types/ws` | 8.18.2 | MIT | Laufzeit |
| `abort-controller` | 3.0.0 | MIT | Laufzeit |
| `aedes` | 1.2.0 | MIT | nur Tests |
| `aedes-packet` | 4.0.1 | MIT | nur Tests |
| `aedes-persistence` | 11.0.0 | MIT | nur Tests |
| `base64-js` | 1.5.1 | MIT | Laufzeit |
| `bl` | 6.1.6 | MIT | Laufzeit |
| `broker-factory` | 3.1.15 | MIT | Laufzeit |
| `buffer` | 6.0.3 | MIT | Laufzeit |
| `buffer-from` | 1.1.2 | MIT | Laufzeit |
| `commist` | 3.2.0 | MIT | Laufzeit |
| `concat-stream` | 2.0.0 | MIT | Laufzeit |
| `readable-stream` | 3.6.2 | MIT | Laufzeit |
| `debug` | 4.4.3 | MIT | Laufzeit |
| `event-target-shim` | 5.0.1 | MIT | Laufzeit |
| `events` | 3.3.0 | MIT | Laufzeit |
| `fast-unique-numbers` | 9.0.27 | MIT | Laufzeit |
| `fastparallel` | 2.4.1 | ISC | nur Tests |
| `help-me` | 5.0.0 | MIT | Laufzeit |
| `ieee754` | 1.2.1 | BSD-3-Clause | Laufzeit |
| `inherits` | 2.0.4 | ISC | Laufzeit |
| `ip-address` | 10.7.6 | MIT | Laufzeit |
| `lru-cache` | 10.4.3 | ISC | Laufzeit |
| `minimist` | 1.2.8 | MIT | Laufzeit |
| `mqemitter` | 7.1.0 | ISC | nur Tests |
| `mqtt` | 5.16.1 | MIT | Laufzeit |
| `mqtt-packet` | 9.0.2 | MIT | Laufzeit |
| `ms` | 2.1.3 | MIT | Laufzeit |
| `process` | 0.11.10 | MIT | Laufzeit |
| `process-nextick-args` | 2.0.1 | MIT | Laufzeit |
| `qlobber` | 8.0.1 | MIT | nur Tests |
| `readable-stream` | 4.8.0 | MIT | Laufzeit |
| `reusify` | 1.1.0 | MIT | nur Tests |
| `rfdc` | 1.4.1 | MIT | Laufzeit |
| `safe-buffer` | 5.2.1 | MIT | Laufzeit |
| `smart-buffer` | 4.2.0 | MIT | Laufzeit |
| `socks` | 2.8.11 | MIT | Laufzeit |
| `split2` | 4.2.0 | ISC | Laufzeit |
| `string_decoder` | 1.3.0 | MIT | Laufzeit |
| `tslib` | 2.9.1 | 0BSD | Laufzeit |
| `typedarray` | 0.0.6 | MIT | Laufzeit |
| `undici-types` | 8.9.0 | MIT | Laufzeit |
| `util-deprecate` | 1.0.2 | MIT | Laufzeit |
| `worker-factory` | 7.0.50 | MIT | Laufzeit |
| `worker-timers` | 8.0.34 | MIT | Laufzeit |
| `worker-timers-broker` | 8.0.18 | MIT | Laufzeit |
| `worker-timers-worker` | 9.0.15 | MIT | Laufzeit |
| `ws` | 8.23.0 | MIT | Laufzeit |
| `xtend` | 4.0.2 | MIT | nur Tests |
