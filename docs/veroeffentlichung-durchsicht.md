# Durchsicht vor Veröffentlichung

Stand: 2026-09-04. Diese Durchsicht trifft **keine Entscheidung**, ob das Repository
öffentlich geschaltet wird — sie listet, was dem heute entgegensteht, damit der
Projektinhaber die Entscheidung „erst mit einer zumutbaren, getesteten Fassung" treffen
kann.

## Was geprüft wurde

- Der Arbeitsbaum: Quelltext, Tests, Dokumentation, Migrationen, Konfigurationsbeispiele,
  `.github/workflows`, `docker/`, `tests/daten/`.
- Die vollständige Git-Historie (`git log -p --all`) auf Zugangsdaten, private Schlüssel,
  reale IP-/MAC-Adressen, E-Mail-Adressen und jemals eingecheckte `.env`-Dateien — auch
  gelöschte Stände, da diese in der Historie weiterhin vorhanden wären.
- `docs/bestandsaufnahme-altsystem.md` auf Angaben zum realen Altsystem, die nicht
  öffentlich werden sollten.
- `docs/offene-entscheidungen.md`, insbesondere den Eintrag zur Anonymisierung von
  `tests/daten/anlage-beispiele.json`, gegen die tatsächliche Datei geprüft.
- Lizenzlage: `pyproject.toml`, Repository-Wurzel, `static/vendor`-Herkunftsnachweis.
- Zumutbarkeit für einen fremden Betreiber: `README.md`, `docs/self-hosting.md`,
  `docker/compose.beispiel.yml`, `.env.example`.
- Die beiden Sicherheitsdurchsichten (`docs/sicherheitsdurchsicht.md`,
  `docs/sicherheitsdurchsicht-2026-09-02.md`) gegen den aktuellen Code und gegen das, was
  `docs/self-hosting.md` einem fremden Betreiber tatsächlich sagt.
- Die Testsuite (Ergebnis siehe unten).

## Was **nicht** geprüft wurde

- Der reale MQTT-Broker, das reale Heimnetz, reale ACLs, TLS-Terminierung oder
  Reverse-Proxy-Konfiguration — das ist Betriebsumgebung, kein Repository-Inhalt.
- Bekannte Schwachstellen in Abhängigkeiten (CVE-/Supply-Chain-Prüfung); das steht
  bereits als offener Punkt in `docs/sicherheitsdurchsicht-2026-09-02.md`.
- Der MariaDB-Testlauf wurde nicht wiederholt (die Aufgabe verlangt nur den einen
  vorgegebenen `pytest`-Lauf); dessen letztes dokumentiertes Ergebnis steht in
  `docs/sicherheitsdurchsicht-2026-09-02.md` (`1618 passed, 1 skipped`).
- Am Code, an Tests oder an bestehender Dokumentation wurde nichts verändert oder
  behoben — diese Runde ist eine Durchsicht, keine Behebung. Einzige Ausnahme, auf
  ausdrücklichen Auftrag: In `docs/sicherheitsdurchsicht-2026-09-02.md` wurden die
  Fix-Status-Marken je Befund gegen den aktuellen Code nachgezogen, siehe „Nachtrag
  (2026-09-04)" dort — kein Befund wurde gestrichen oder gekürzt, nur ergänzt.

---

## Befunde

### Hoch — keine Lizenz

**Nachtrag (2026-09-04): behoben.** Der Projektinhaber hat sich für die GNU Affero
General Public License, Version 3 (AGPL-3.0-only) entschieden. `LICENSE` enthält jetzt
den unveränderten Lizenztext von gnu.org, `pyproject.toml` trägt `license =
"AGPL-3.0-only"` samt `license-files = ["LICENSE"]` (SPDX-Form, geprüft mit
`setuptools>=77` — die Datei landet nachweislich unter `*.dist-info/licenses/LICENSE`
im gebauten Wheel), `README.md` hat einen eigenen Abschnitt „Lizenz", und die
OpenAPI-Beschreibung unter `/docs` führt die Lizenz ebenfalls. Offen bleibt §13 AGPL
(Quelltext-Hinweis in der Oberfläche für netzseitige Nutzung) — dafür fehlt noch die
öffentliche Repository-Adresse; das ist als eigener Punkt vorgemerkt, sobald sie
feststeht. Der ursprüngliche Befund bleibt unten unverändert stehen, damit
nachvollziehbar bleibt, dass er bestand.

**Was:** Es gibt weder eine `LICENSE`-Datei im Repository-Wurzelverzeichnis noch ein
`license`-Feld in `pyproject.toml`, noch eine Aussage in `README.md`, unter welchen
Bedingungen der Code genutzt, verändert oder weiterverbreitet werden darf.

**Datei:** nicht vorhanden (geprüft: Repository-Wurzel, `pyproject.toml`).

**Warum das einer Veröffentlichung entgegensteht:** Ohne Lizenz gilt automatisch das
volle Urheberrecht — rechtlich darf ein Dritter den Code weder benutzen noch verändern
noch weitergeben, auch wenn das Repository öffentlich einsehbar ist. Das widerspricht dem
erkennbaren Zweck der Veröffentlichung. `thermoctl/web/static/HERKUNFT.md` dokumentiert
vorbildlich die Lizenzen der mitgelieferten Fremdbestandteile (Bootstrap MIT, HTMX
BSD-2-Clause, Swagger UI Apache 2.0) — der eigene Code hat aber keine.

**Was zu tun wäre:** Eine Lizenz wählen (siehe „was nur der Projektinhaber entscheiden
kann" unten) und als `LICENSE`-Datei plus `license`-Feld in `pyproject.toml` ablegen.

---

### Mittel — Ein Teil der noch offenen Befunde aus der Durchsicht vom 2026-09-02 steht nicht in der für Fremde bestimmten Dokumentation

**Korrektur gegenüber der ersten Fassung dieses Abschnitts:** Die erste Fassung
behauptete, vier „Hoch"-Befunde aus `docs/sicherheitsdurchsicht-2026-09-02.md` seien
unbehoben. Das war falsch. Auf ausdrücklichen Hinweis wurde jeder Hoch- und
Mittel-Befund dieses Dokuments einzeln gegen den aktuellen Code und die zugehörigen
Tests nachgeprüft (nicht gegen die Marken im Dokument selbst, die teils veraltet waren)
— das Ergebnis steht jetzt auch direkt im geprüften Dokument, als „Nachtrag
(2026-09-04)" bei jedem Befund. Tatsächlicher Stand:

**Behoben, mit Regressionstest belegt** (sechs von acht Hoch-/Mittel-Befunden):

- `device.manage` einer Zone erreichte fremde Zonen (Commit `8501b0e`) —
  `tests/test_security_review_2026_09_02.py::test_device_manage_einer_zone_erreicht_keine_fremde_zone`
  und `::test_tastenbelegung_verlangt_das_recht_fuer_jede_zone_des_geraets`.
- Ein Kiosk-Token galt außerhalb des Kiosks als vollwertiger REST-/MCP-Bearer
  (Commit `8501b0e`) — `::test_kiosk_token_gilt_nicht_als_rest_bearer`,
  `::test_kiosk_token_gilt_auch_bei_mcp_nicht`.
- Unangemeldete Login-Anfragen konnten den Regelzyklus blockieren, per synchronem
  `time.sleep()` im `async`-Handler (Commit `cd9563e`) —
  `tests/test_login.py::test_the_login_delay_does_not_block_the_event_loop`,
  `::test_the_password_check_does_not_run_on_the_event_loop`.
- Ein Passwortwechsel beendete gestohlene Sitzungen nicht (Commit `cd9563e`) —
  `tests/test_login.py::test_changing_the_own_password_ends_the_other_sessions_only`,
  `::test_ending_the_other_sessions_without_changing_the_password`.
- Meross bestätigte nur den Befehl, nicht den Zielzustand (bereits in der Durchsicht
  selbst als „behoben" geführt) — `tests/test_meross_mqtt.py`, `tests/test_actuators.py`.
- Der Webhook folgte Weiterleitungen und nahm `Authorization` mit (bereits als
  „behoben" geführt) — `tests/test_notification.py`,
  `tests/test_security_review_2026_09_02.py::test_offen_webhook_redirect_nimmt_authorization_an_internes_ziel_mit`.
- Die Bediengeräteseite verriet den vollständigen Gerätebestand (Commit `8501b0e`,
  Niedrig) —
  `::test_bediengeraeteseite_verraet_keine_fremden_geraetenamen`,
  `::test_bediengeraeteseite_verlangt_geraeteleserecht`.

**Teilweise behoben:**

- Das Einrichtungs-Token läuft jetzt nach einer Stunde ab (Commit `cd9563e`,
  `tests/test_setup.py::test_an_expired_setup_token_no_longer_sets_anything_up`) und
  kann daher nicht mehr „beliebig alt" sein. Es steht aber weiterhin absichtlich im
  Klartext im Log — das bleibt die eine dokumentierte Ausnahme vom Grundsatz „kein
  Geheimnis im Log" und ist bereits in `docs/self-hosting.md` benannt.

**Tatsächlich noch offen und ohne Erwähnung in `README.md` oder
`docs/self-hosting.md`:**

1. **Mittel** — Meross-HTTP-Antworten sind größenmäßig unbeschränkt
   (`thermoctl/integrations/meross.py::UrllibJsonTransport._post_sync`, `answer.read()`
   ohne Limit), und Fehlertext aus der Cloud landet unter dem Schlüssel `grund`
   unmaskiert im Log (`thermoctl/services/meross_discovery.py:168`,
   `thermoctl/services/meross_session.py:122`).
2. **Mittel** — Das Schaltprotokoll (`device_command`) wächst ohne
   Aufbewahrungsgrenze; `thermoctl/services/retention.py` bereinigt nur Messwerte und
   Schattenentscheidungen, keine Schaltprotokoll-Zeilen.
3. **Niedrig** — Die CSRF-Ausnahme für `/login` und `/logout` erlaubt Abmelde- und
   Anmelde-CSRF ohne zusätzliche `Origin`-Prüfung; die Durchsicht selbst stuft die
   körperliche Wirkung als gering ein.

Zwei Hoch-Befunde bleiben ebenfalls offen, sind aber bereits dokumentiert: die
HTTP-`0.0.0.0`-Vorgabe ohne TLS-Erzwingung (`docs/self-hosting.md`, Abschnitt 4) und die
MQTT-Vertrauensgrenze, die Code nicht erzwingen kann
(`docs/mqtt.md#den-broker-absichern-er-ist-eine-vertrauensgrenze`).

**Warum das noch einer Veröffentlichung entgegensteht:** Die drei tatsächlich offenen
und unerwähnten Punkte sind Mittel/Niedrig, nicht Hoch — deutlich geringeres Gewicht als
in der ursprünglichen Fassung dieses Befunds behauptet. Sie betreffen aber echte,
unbehobene Eigenschaften: Ein fremder Betreiber mit hinterlegtem Meross-Konto erfährt
nirgends, dass eine kompromittierte oder falsch konfigurierte Cloud-Gegenstelle Speicher
oder Log-Inhalte beeinflussen kann, und niemand erfährt, dass das Schaltprotokoll
unbegrenzt wächst und eigenständig überwacht werden muss.

**Was zu tun wäre (nicht in dieser Runde):** Die drei verbleibenden Punkte entweder vor
der Veröffentlichung beheben oder in `docs/self-hosting.md` unter „bekannte
Einschränkungen" benennen, so wie es für HTTP-Vorgabe, MQTT-Vertrauensgrenze und
Einrichtungs-Token bereits geschieht. Zusätzlich: `docs/sicherheitsdurchsicht.md` (die
ältere, in der Navigation verlinkte Fassung) gegenüber
`docs/sicherheitsdurchsicht-2026-09-02.md` als überholt kennzeichnen oder
zusammenführen — README verlinkt aktuell nur die ältere, was das Risiko erhöht, dass ein
Leser die neuere Fassung mit dem tatsächlichen Stand gar nicht findet. Unabhängig davon
war `docs/sicherheitsdurchsicht-2026-09-02.md` selbst vor dieser Prüfung an sechs
Stellen veraltet — behobene Befunde standen dort weiter als offen. Das ist in dieser
Runde direkt im Dokument nachgezogen worden (siehe „Nachtrag (2026-09-04)" bei jedem
betroffenen Befund), ist aber selbst ein Hinweis, dass Sicherheitsdokumentation in
diesem Projekt Gefahr läuft, hinter Fix-Commits zurückzubleiben, wenn beides nicht im
selben Commit nachgezogen wird — vergleichbar mit der Regel, die `CLAUDE.md` bereits für
`STATUS.md` festhält.

---

### Mittel — private IP-Adresse des Altsystems im Repository

**Erledigt (2026-09-04):** `docs/bestandsaufnahme-altsystem.md` ist mitsamt den übrigen
Projektinterna aus dem veröffentlichten Repository herausgelöst (bleibt lokal erhalten,
siehe `.gitignore`). Die private IP ist damit nicht mehr Teil dessen, was veröffentlicht
wird. Der Befund bleibt unten stehen, damit nachvollziehbar bleibt, dass er einmal bestand.

**Was:** `docs/bestandsaufnahme-altsystem.md:159` nennt die interne Adresse der
MariaDB-Instanz des Altsystems im Klartext: „MariaDB (`192.168.0.130`)".

**Datei und Zeile:** `docs/bestandsaufnahme-altsystem.md:159`.

**Warum das fraglich ist:** Es ist eine private RFC1918-Adresse, aus dem offenen Internet
nicht direkt erreichbar — kein Secret im engeren Sinn. Sie ist aber eine reale,
identifizierende Eigenschaft der tatsächlichen Wohnung/Anlage (Netzwerk-Nummerierung,
möglicherweise sogar Namensursprung des Hostnamens des Altsystems im selben Dokument), und sie ist
für den Zweck des Dokuments — den Zustand des Altsystems für spätere Sessions
festzuhalten — nicht notwendig. Sie fällt unter denselben Gedanken wie Grundsatz 2
(„keine Broker-Adressen … im Quelltext"), auch wenn es sich um Dokumentation statt Code
handelt.

**Was zu tun wäre:** Durch einen Platzhalter ersetzen oder streichen; der Rest des
Dokuments (Schema, Fallstricke, Topic-Vertrag) bleibt davon unberührt.

---

### Niedrig — Repository-Adresse im Beispiel-Compose ist bereits scharf mit einem realen Kontonamen verdrahtet

**Was:** `docker/compose.beispiel.yml:17` verweist auf
`image: ghcr.io/magicalwig34653/thermoctl:latest` — ein konkreter GitHub-Kontoname statt
eines Platzhalters wie an anderen Stellen der Datei (`.env.example` verwendet
durchgängig `<…>`-Platzhalter oder Vorgabewerte ohne echte Kontobezüge).

**Datei und Zeile:** `docker/compose.beispiel.yml:17`.

**Warum das erwähnenswert ist:** Kein Sicherheitsproblem — wenn dieses Konto das ist,
unter dem das Repository ohnehin veröffentlicht werden soll, ist der Verweis korrekt und
sogar hilfreich (copy-paste-fähig). Es ist aber eine bewusste Verknüpfung des
Repository-Inhalts mit einem konkreten, vermutlich bereits öffentlichen Kontonamen. Wert,
kurz zu bestätigen, dass dies der beabsichtigte, öffentliche Name ist und keine
Verwechslung mit einem anderen, nicht für diesen Zweck vorgesehenen Konto.

**Was zu tun wäre:** Nur eine Bestätigung durch den Projektinhaber, dass der Kontoname so
gewollt ist — sonst durch einen Platzhalter ersetzen.

---

## Geprüft und unauffällig

- **Git-Historie vollständig auf Secrets durchsucht** (`git log -p --all`, alle Branches):
  keine `.env`-Datei jemals eingecheckt, keine privaten Schlüssel (`BEGIN … PRIVATE KEY`),
  keine Passwörter/API-Schlüssel außerhalb von Test- und Dokumentationsplatzhaltern, keine
  MAC-Adressen. Gefundene E-Mail-Adressen sind ausschließlich `*.example.invalid`,
  `*.local`-Testadressen oder offensichtliche Fixture-Werte (`a@b.de`, `k@example.invalid`)
  — keine reale Adresse.
- **IP-Adressen in Historie und Arbeitsbaum**: bis auf den oben genannten Befund
  ausschließlich `127.0.0.1`, `0.0.0.0` und die dokumentationskonformen TEST-NET-Adressen
  `198.51.100.42` / `192.0.2.x`.
- **`tests/daten/anlage-beispiele.json`**: enthält generische Raumbezeichnungen
  („Bad", „Küche", „Zimmer 1/2/3", „Balkon") ohne Vornamen oder unterscheidbare
  Personenbezüge — der Eintrag in `docs/offene-entscheidungen.md` zur Anonymisierung
  stimmt weiterhin. Die Originaldatei liegt nachweislich außerhalb des Repositories
  (`.superpowers/sdd/` ist nirgends in der Git-Historie aufgetaucht).
- **Grundsatz 1 (nichts hart verdrahtet)**: keine Geräte-IDs, Raumnamen oder
  Broker-Adressen im Quelltext (`thermoctl/`); Meross-Zugangsdaten kommen ausschließlich
  aus `settings` (`thermoctl/config.py`), nirgends als Fallback-Wert.
- **Vendor-Bestandteile** (`thermoctl/web/static/vendor/`): Bootstrap, HTMX, Swagger UI —
  vollständig mit Quelle, Version, Lizenz und Prüfsumme in
  `thermoctl/web/static/HERKUNFT.md` dokumentiert.
- **Meross-`APP_SECRET`**: bereits in `docs/sicherheitsdurchsicht.md` als kein
  Zugangsdatum der Anlage eingeordnet, sondern eine seit Jahren durch Reverse
  Engineering öffentliche Protokollkonstante — nachvollzogen und nicht neu zu bewerten.
- **`.gitignore`**: deckt `.env`, `.env.*` (mit Ausnahme `.env.example`), `*.db`,
  `data/`, `logs/` — konsistent mit dem Befund, dass nie eine echte `.env` eingecheckt
  wurde.
- **`docs/bestandsaufnahme-altsystem.md`** im Übrigen: Schema, Fallstricke,
  MQTT-Topic-Vertrag, HTTP-Routen des Altsystems — technische Ist-Beschreibung ohne
  Personenbezug; einzige Ausnahme die oben genannte IP-Adresse.
- **Zumutbarkeit für Fremde**: `docs/self-hosting.md` führt in 11 Abschnitten von
  Docker-Voraussetzung über Einrichtung, TLS-Pflicht bei Netzexposition, Sicherung,
  Aktualisierung/Rückweg, PI-Beta-Warnung, Fehlerbehebung, Kiosk, Passkeys bis zur
  API-Erkundung — inhaltlich vollständig und mit expliziter Warnung „Der Dienst steuert
  eine Heizung in einer bewohnten Wohnung" sowie „Ins offene Internet gehört dieser
  Dienst nicht". PI wird korrekt als Beta mit Trockenlauf-Vorgabe benannt.
  `docker/compose.beispiel.yml` ist inhaltlich vollständig (SQLite- und
  MariaDB-Variante, Healthcheck, Loopback-Bindung mit Begründung).
- **Testsuite**: siehe Prüflauf unten — läuft durch, 100 % Testabdeckung ausgewiesen.

---

## Prüflauf

```
cd "/Users/linolaske/Documents/Code Projekte/PycharmProjects/thermoctl-worktrees/veroeffentlichung"
.venv/bin/python -m pytest -q
```

Ergebnis: Exit-Code `0`. Die gesamte Ausgabe bestand aus Fortschrittspunkten (keine `F`/`E`
in der Fortschrittsanzeige) gefolgt vom Abdeckungsbericht mit `TOTAL 7343 0 100%` — jede
Zeile unter `thermoctl/` gilt laut Bericht als ausgeführt oder ausdrücklich mit
`# pragma: no cover` begründet ausgenommen. Aus einem separaten Lauf mit
`--collect-only` lässt sich die Gesamtzahl der Testfunktionen nicht direkt ablesen; nach
dem letzten dokumentierten Vergleichslauf in
`docs/sicherheitsdurchsicht-2026-09-02.md` waren es zuletzt `1619 passed` (SQLite) bzw.
`1618 passed, 1 skipped` (MariaDB) — dieser Lauf wurde in dieser Runde nicht gegen
MariaDB wiederholt, siehe „Was nicht geprüft wurde".

---

## Was nur der Projektinhaber entscheiden kann

1. **Lizenzwahl.** Ob und unter welcher Lizenz (MIT, Apache 2.0, AGPL wegen des
   Netzwerkdienst-Charakters, oder etwas anderes) — das ist eine rechtliche und
   strategische Entscheidung, die diese Durchsicht nicht treffen kann. Ohne sie bleibt
   der Befund „keine Lizenz" oben bestehen. **Nachtrag (2026-09-04): entschieden** —
   AGPL-3.0-only, siehe Nachtrag beim Befund oben.
2. **Ob die vier offenen Hoch-Befunde aus der Sicherheitsdurchsicht vom 2026-09-02 vor
   der Veröffentlichung behoben werden müssen, oder ob eine dokumentierte, bekannte
   Einschränkung für eine erste öffentliche Fassung ausreicht.** Das ist eine Abwägung
   zwischen Aufwand und Risiko, die von der Zielgruppe abhängt (private Nutzung im
   eigenen Heimnetz vs. eine Person, die den Dienst versehentlich exponiert).
3. **Ob `docs/sicherheitsdurchsicht.md` (ältere Fassung) durch die neuere ersetzt,
   zusammengeführt oder beide mit einem klaren Gültigkeitshinweis verlinkt werden** —
   aktuell verlinkt `README.md` nur die ältere.
4. **Der GitHub-Kontoname in `docker/compose.beispiel.yml`** — Bestätigung, dass
   `magicalwig34653` der für dieses Projekt vorgesehene öffentliche Name ist.
5. ~~Die private IP-Adresse des Altsystems~~ — **erledigt (2026-09-04):** Das Dokument, in
   dem sie stand, ist mitsamt den Projektinterna aus dem Repository herausgelöst; die
   Abwägung erübrigt sich damit.

---

## Zusammenfassung: was steht heute im Weg, was nicht

**Kein echtes Secret gefunden** — weder im Arbeitsbaum noch in der vollständigen
Git-Historie. Das war das Risiko mit dem größten Schadenspotential (unwiderruflich, sobald
das Repository einmal öffentlich war) und ist nach dieser Durchsicht nicht belegt.

**Was heute entgegensteht:**

- Es gibt keine Lizenz — rechtlich blockierend für eine sinnvolle Veröffentlichung, unabhängig vom technischen Zustand. **Nachtrag (2026-09-04): behoben**, AGPL-3.0-only, siehe Nachtrag beim Befund oben.
- Drei tatsächlich noch offene Befunde (zwei Mittel, ein Niedrig) aus der letzten
  Sicherheitsdurchsicht sind einem fremden Leser von `README.md`/`docs/self-hosting.md`
  nicht zugänglich: unbegrenzte/unmaskierte Meross-Cloud-Antworten, das unbegrenzt
  wachsende Schaltprotokoll, und die CSRF-Ausnahme für Login/Logout. Sechs von acht
  Hoch-/Mittel-Befunden dieser Durchsicht sind dagegen bereits behoben und mit
  Regressionstests belegt — die erste Fassung dieses Abschnitts hatte das falsch
  eingeschätzt, siehe der korrigierte Befund oben.
- ~~Ein Detail des realen Altsystems (private IP) ist ohne erkennbaren Nutzen für den
  Dokumentationszweck im Repository.~~ **Erledigt (2026-09-04):** mit der Datei aus dem
  Repository herausgelöst.

**Was nicht entgegensteht:**

- Secrets und Zugangsdaten — sauber, auch historisch.
- Personen- und Anlagenbezug in Testdaten — die dokumentierte Anonymisierung hält.
- Hartverdrahtetes im Quelltext — keines gefunden.
- Fremdcode-Lizenzen (`static/vendor/`) — vorbildlich dokumentiert.
- Die Testsuite — läuft grün mit 100 % ausgewiesener Abdeckung.
- Die Selbsthosting-Dokumentation für den bereits bekannten, bereits dokumentierten Teil
  der Sicherheitslage (HTTP-Vorgabe, MQTT-Vertrauensgrenze, Einrichtungs-Token) —
  vollständig und mit angemessenen Warnungen.
- Die sechs behobenen Rechte-/Verfügbarkeitsbefunde der Sicherheitsdurchsicht vom
  2026-09-02 — jeder einzeln gegen Code und Regressionstest nachgeprüft, nicht nur gegen
  die (teils veralteten) Marken im Dokument.
