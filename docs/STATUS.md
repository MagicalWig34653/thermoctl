# Stand

Letzte Aktualisierung: 2026-09-10.

## Die CI baut jetzt auch das Image

CLAUDE.md nennt den Docker-Image-Bau als Freigabe-Riegel; in
`.github/workflows/ci.yml` fehlte er als Einziges der dort genannten Punkte. Er
steht jetzt als eigener Job `docker-build` neben der Datenbank-Matrix, nicht in
ihr: SQLite und MariaDB ergeben dasselbe Image, und zweimal dasselbe zu bauen
kostet Laufzeit ohne eine zweite Aussage. Gebaut, nicht veröffentlicht -- keine
Anmeldung, keine Registry, kein Upload, `permissions: contents: read` unverändert.

## v0.9.0 -- Freigabevorbereitung: getrennte Admin- und Mieteroberfläche

Die Arbeitsanweisung zum Redesign liegt unter
`docs/ui-redesign/` (Zielbild als HTML-Demos, Bestandsaufnahme der heutigen WebUI,
`IMPLEMENTATION.md` mit der Definition of Done).

**Fertig: das UI-Profil.** `access_group.ui_profile` (`admin` / `tenant`, Migration
`c4d18b7e2a95`) entscheidet, **welche** Oberfläche jemand bekommt -- ausdrücklich
nicht, **was** er darf. Das bleibt allein Sache der Grants. Beide Prüfungen laufen
hintereinander: der Wächter `web/guards.py::require_web_ui_profile` hängt als
Router-Dependency vor den Anlagenseiten, jede vorhandene `require(...)`- und
`visible_zones(...)`-Prüfung in den Endpunkten bleibt unverändert bestehen.

Warum das Profil an der Gruppe hängt und nicht am Benutzer: dort hängen schon die
Rechte, und ein zweiter Zuordnungsweg wäre eine zweite Wahrheit darüber, wer wozu
gehört. Ein Benutzer in mehreren Gruppen ist nur dann Mieter, wenn **alle** seine
Gruppen Mietergruppen sind (`domain/ui_profile.py::combined_profile`).

**Beim Upgrade wird nichts umklassifiziert.** Bestehende Gruppen bekommen `admin` --
auch eine, die "Mieter" heißt. Ein Name ist kein Modell, und eine Anlage, die nach
`alembic upgrade head` ihre Verwaltung verliert, wäre der teuerste denkbare
Migrationsfehler (Test: `test_migrations.py::
test_existing_groups_keep_the_admin_interface_on_upgrade`). Die Einrichtung legt
zusätzlich die Vorlage "Wohnung" an -- Mieterprofil, aber **kein einziges Recht**;
welche Zonen dazugehören, trägt die Verwaltung danach ein.

Die Navigationstabelle (`web/navigation.py`) trägt jetzt Abschnitt und Profil je
Eintrag und ist nach den vier Blöcken der Admin-Demo geordnet (Hauptbereich,
Analyse, System, Zugänge). Die Startseite und `/statistics` sind dabei neu in die
Tabelle gekommen.

**Fertig: die Hüllen.** `base_core.html` trägt, was beide Oberflächen teilen --
Kopfbereich, Farbschema, Assets, HTMX, CSRF-Weitergabe, Ladeanzeige, der Hinweis auf
eine veraltete Seite. Darauf setzen `base_admin.html` (Seitenleiste am Desktop,
reduzierte Fußleiste mobil) und `base_tenant.html` (mobile first). Die bisherige
base.html ist entfernt; alle Vorlagen verwenden die jeweiligen Hüllen.
`base_plain.html` (Kiosk) bleibt bewusst daneben und erbt nichts davon --
ein Wandtablett hat weder Navigation noch Ladebalken, und seine CSRF-Behandlung ist
eine andere.

**Fertig: der persönliche Bereich** `/account` (`web/account_views.py`) -- eigenes
Passwort, andere Sitzungen beenden, Passkeys, Hilfe, Abmelden. Ohne Profil-Wächter,
weil er beiden Oberflächen gehört, und ohne jedes Recht: das eigene Passwort zu
ändern ist kein privilegierter Vorgang. Bis hierher lagen diese beiden Funktionen auf
der Benutzerverwaltungsseite und wären für ein Mieterprofil unerreichbar gewesen.
`/account/help` erklärt die Anzeigen in Alltagssprache und liest dafür ausdrücklich
nichts aus der Datenbank -- eine Seite ohne Rechteprüfung darf keine Zonennamen
zeigen.

**Fertig: die Wohnungssicht.** `/` verzweigt nach `principal.ui_profile` --
dieselbe Adresse, zwei Oberflächen. Dazu `/schedule` (Wochenplan je eigenem Raum,
24-Stunden-Vorschau aus `schedule_forecast`) und `/heating-time` (7/30/90 Tage, nur
sichtbare Räume, ausdrücklich „Heizzeit" und im Trockenlauf „hätte geheizt").
Alle drei mit dem Wächter `tenant_ui_only`.

Die Raumkarte zeigt Raumname, Entscheidung, Isttemperatur samt Alter, Modus,
Sollwert mit verständlicher Begründung, Tagesverlauf, „Als Nächstes", den
dauerhaften ±0,5-K-Schritt am laufenden Modus, „Für eine Weile wärmer" und „Zur
nächsten Schaltzeit springen". **Thermostat und Übersteuerung bleiben auch hier
getrennt**, samt der Beschriftung, welcher Modus dauerhaft geändert wird.

Die Bedienelemente rufen die vorhandenen Endpunkte aus `daily_views.shared_router`
-- derselbe Weg wie die Anlagensicht, über zonenbezogene Rechte abgesichert und ohne
Profil-Wächter, weil sie beiden Oberflächen gehören. Neu dort:
`POST /zones/{id}/jump-next` über `domain.schedule.jump_to_next_switch`.

Der Zeitplan der Wohnungssicht ruft **dieselben** Domänenfunktionen wie der
Admin-Editor (`copy_schedule_day`, `adopt_schedule`, `undo_schedule_gesture`,
`move_schedule_point` …) -- eine einfachere Oberfläche auf dieselben Daten, keine
zweite Zeitplanmechanik. Geschrieben wird mit `schedule.manage` **je Zone**; ein
Mieter bekommt dafür ausdrücklich kein `zone.manage`.

`domain.statistics.PERIODS` hält die drei Zeiträume jetzt an einer Stelle für beide
Auswertungen.

**Zonenisolation** ist die Zusicherung, die `tests/test_tenant_views.py` am
gründlichsten prüft: jede Zonen-Id aus Pfad oder Formular wird erneut gegen
`visible_zones` geprüft und ergibt sonst **404** -- nie 403, das den Unterschied
zwischen „gibt es nicht" und „gehört jemand anderem" verriete. Geprüft wird auf
Anzeigename **und** Id im gesamten HTML, auch als Quelle einer Übernahme.

**Fertig: Abwesenheit.** Ein Mieter setzt einen Zeitraum an, in dem seine Räume
sparsamer geregelt werden -- `POST /absence`, beendet über `POST /absence/end`.
Welche Räume betroffen sind, entscheidet **ausschließlich der Server**
(`visible_zones(..., "override.create")`); es gibt bewusst kein Formularfeld dafür.
Ausdrücklich nicht der anlagenweite Urlaubsbetrieb: der senkt auch die Räume anderer
Mieter ab. Umgesetzt über die vorhandene Übersteuerungs-Domäne, mit `absence` als
Klammer darum (`zone_override.absence_id`), damit sich die Gruppe als *eine*
Abwesenheit anzeigen und in *einem* Schritt beenden lässt. Alles oder nichts:
entweder entstehen Klammer und alle Übersteuerungen, oder gar nichts.

**Fertig: „Problem melden".** Keine Attrappe -- die Meldung geht über dieselbe
Meldekette wie jede Störungsmeldung (der vom Betreiber konfigurierte Webhook) und
steht im Audit. Eigenes zonenbezogenes Recht **`report.create`** (Migration
`d31f6a04c7e9`), ausdrücklich nicht `zone.read`: etwas nach außen auszulösen darf
nicht aus einem Leserecht folgen. Die Migration teilt das Recht **keiner** Gruppe
automatisch zu. Sechster Meldungsschalter `setting.notify_tenant_reports` auf
`/settings`. Ist kein Webhook eingerichtet oder der Schalter aus, wird die Meldung
trotzdem im Audit festgehalten und der Mieter erfährt ehrlich, dass es keine
automatische Weiterleitung gibt.

Was der Bericht enthält, steht abschließend im Docstring von
`domain/problem_report.py` -- Raum, Problemart, Hinweis, Zeitpunkt, letzter
Messwert, Sollwert samt Begründung, Modus, Melder. Und nichts sonst: keine
Zugangsdaten, keine Brokeradressen, keine Gerätebezeichner, nichts aus einer anderen
Zone. Ein Test legt eine zweite Zone mit auffälligem Namen an und prüft es.

`age_in_words` ist dabei von `web/__init__.py` nach `domain/time.py` gewandert: die
Meldung braucht dieselbe Formulierung, und die Domäne darf keinen Adapter
importieren (`test_architecture.py::test_the_domain_knows_no_adapter`).

**Verschoben, mit Begründung:** persönliche Störungsbenachrichtigungen je Mieter.
thermoctl hat keinen individuellen Zustellkanal -- der Webhook ist anlagenweit und
geht an den Betreiber, nicht an einzelne Bewohner. Ein Schalter „Wichtige Störungen"
in der Wohnungssicht wäre eine funktionslose Einstellung. Die Hinweise erscheinen
stattdessen in der Oberfläche selbst (Startseite, oberer Hinweisbereich). Ein echter
persönlicher Kanal wäre eine eigene Aufgabe.

Der Rückbau der Abwesenheitsmigration entfernt zuerst den Fremdschlüssel und dann
den zugehörigen Index, wie InnoDB es verlangt. Die aktuellen Prüfergebnisse stehen
unter „Zahlen“.

## Optik: die Palette und die Formen der Demos übernommen

Übernommen ist jetzt die Gestaltung beider Demos, über die **Tokens**: dieselben
Namen wie vorher, andere Werte, sodass der gesamte Bestand ihnen folgt. Die
Anlagensicht führt Blau als Primärfarbe (`#2463eb`), die Wohnungssicht gedämpftes
Grün (`#236248`) -- gesetzt am Rumpf über `.tc-tenant`, damit beide Hüllen dieselben
Bausteine benutzen und nur ihre Werte tauschen. Dazu: Ecken von 16 bzw. 20 px,
getragene Schatten, Zustandsmarken als Pillen in kleinen Versalien, große und eng
gesetzte Seitenüberschriften, Tabellenköpfe auf eigener Fläche, Zonen als
**Kartenraster** statt als Zeilenband.

Temperaturflächen -- Tagesspur, Wochenplan, Vorschau -- verwenden
`--warmth` (Orange) und `--cool` (Blau) als Messwertskala. Die Primärfarbe für
Bedienelemente ist davon getrennt; in der Anlagensicht ist auch sie blau. Alle drei
Ansichten desselben Zeitplans (Startseite, Admin-Wochenplan, Mieter-Wochenplan)
benutzen jetzt dieselbe zweiseitige Skala: unter der Mitte kühl, darüber warm, die
Sättigung sagt wie deutlich.

Der Kiosk verwendet dieselben gültigen Gestaltungsvariablen; die Uhr des
Wandtabletts nutzt die Instrumentschrift.

## Vollständige Rechte nach der Einrichtung

Die Seed-Revision `3685e30419a4_nachschlagetabellen` enthält eine feste Liste der
ursprünglichen Rechte mit ihren damaligen ASCII-Beschreibungen. `report.create`
steht am Ende von `PERMISSIONS` und wird über seine eigene Migration ergänzt.
`test_migrations.py::test_every_permission_exists_after_a_full_upgrade` prüft
nach einem vollständigen Upgrade, dass jedes Recht aus `PERMISSIONS` in der
Tabelle steht; damit ist auch `audit.read` bei einer frischen Einrichtung vorhanden.

## Browsertests

`browser_tests/` (Playwright) ist auf die neue Oberfläche gezogen: `.tc-head` gibt es
nicht mehr, der Anker ist `.tc-topbar` (nicht `.tc-sidebar` -- die klappt unter 992 px
weg), das Sammelmenü „Einstellungen" ist durch die Seitenleiste ersetzt. Neu:
`test_tenant_ui.py` mit sechs Tests der Wohnungssicht (richtige Hülle, vier Bereiche
erreichbar, Sollwert-Stepper rechnet serverseitig, `/settings` ergibt 403, Fußleiste
auf 390 px, Ladebalken vorhanden) und ein Test, dass die Seitenleiste auf schmalem
Bildschirm über den Knopf auf- und zugeht.

Die drei Tests, die prüfen, ob `thermoctl.css` überhaupt wirkt, hängen nicht mehr an
einem festen Farbwert -- der scheitert bei jeder gewollten Farbanpassung und damit
aus dem falschen Grund, und seit dem Redesign wäre er zusätzlich stumpf, weil die
Primärfarbe selbst ein Blau ist. Geprüft wird jetzt, dass die Gestaltungsvariablen
auf `:root` überhaupt ankommen; die kennt nur diese Datei.

Bisher dokumentierter Prüfstand: **56 Browsertests grün**; bei dieser
Dokumentationsfreigabe nicht erneut ausgeführt.

## Mieter-Zeitplan: bearbeiten und leere Tage einrichten

Ohne ausdrückliche Raumwahl zeigt `/schedule` bevorzugt den ersten Raum mit
`schedule.manage`, sonst den ersten lesbaren Raum. Der Auf/Zu-Knopf einer Tageszeile ist als Bedienelement gestaltet.
Ein nur lesbarer Raum nennt die Einschränkung ausdrücklich und verweist auf die
bearbeitbaren Räume.

Der vereinfachte Editor bietet zwei Schaltzeiten je Tag („warm ab“, „kühler ab“).
Ein frisch angelegter Raum hat noch keine Schaltpunkte.

Hat ein Tag **keine** Schaltzeit, bietet die Seite jetzt dieselben zwei Felder an und
legt beide Punkte an. **Welche zwei Modi das sind, entscheidet der Server**
(`_modes_for_an_empty_day`): die beiden wärmsten Sollwerte dieser Zone, der wärmere
zuerst, der Frostschutz ausgenommen -- er ist die untere Schranke der Regelung und
kein Abschnitt eines Tagesablaufs. Eine Modus-Id aus dem Formular wird ignoriert; sie
wäre eine weitere Angabe, der man nicht glauben darf, und brächte nichts, was der
Server nicht ohnehin weiß.

Sind für den Raum noch keine zwei Temperaturen hinterlegt, erscheint kein Knopf,
sondern der Grund. Tage mit einer anderen Punktzahl als null oder zwei bleiben
weiterhin lesbar, aber ohne die vereinfachte Bearbeitung.

## Abgesicherte Randfälle der neuen Oberfläche

- Alle lokalen Formularziele berücksichtigen `url_prefix`, einschließlich des
  Zonenformulars. Ein Wächtertest prüft dies direkt an allen Vorlagen.
- `domain/modes.py::step_setpoint` ändert den Sollwert samt Grenzen mit einer
  atomaren Anweisung; gleichzeitige Thermostat-Klicks gehen nicht verloren.
- Die Auswahl der laufenden Übersteuerung berücksichtigt Beginn und Ende. Nach
  einer kurzen Übersteuerung gilt eine ältere, weiterhin laufende wieder.
- Die Zeitplan-Übernahme prüft `schedule.manage` an der Zielzone.
- „Abwesenheit beenden“ beendet alle laufenden Abwesenheiten und auch deren noch
  nicht begonnene Übersteuerungen. Gleichzeitige Anfragen können weiterhin mehrere
  Abwesenheitsklammern anlegen; die Beendigung macht sie gemeinsam unwirksam.
  `resolved_setpoint` liest dafür die Übersteuerungen, nicht `absence.cancelled_at`.
- Der Freitextfilter der Problemmeldung entfernt unsichtbare Steuerzeichen anhand
  ihrer Unicode-Kategorie und kürzt an Zeichengrenzen.

**Unabhängig sicherheitsdurchgesehen, kein Befund.** Ein Gegenleser, der nicht
umgesetzt hat, hat elf Punkte am Code geprüft: Mieter an Anlagenrouten, ob der
Profil-Wächter irgendwo eine Rechteprüfung ersetzt, Zonen-Leaks über Titel, Auswahlfelder,
versteckte Felder und Weiterleitungsziele, die Bulk-Aktion Abwesenheit samt Teilzuständen
und gelöschten Zonen, Schreibrechte, den Inhalt der Problemmeldung samt Manipulation über
den Freitext, CSRF, die drei Migrationen, den Kiosk, das Token-Profil in REST und MCP, und
Domänenlogik im Browser -- und die Suite selbst ausgeführt.

**Bewusst nicht geändert:** REST, MCP und Kiosk benutzen für „nächste Schaltung
vorziehen" weiterhin `domain/remote_control.py::boost`, das bei einer laufenden
Übersteuerung still eine zweite danebenlegt, während die neue Oberfläche über
`jump_to_next_switch` eine ausdrückliche Ersetzung verlangt. Das anzugleichen wäre
eine Verhaltensänderung am REST-Vertrag und gehört nicht in dieses Teilprojekt --
festgehalten als eigene Folgearbeit.

`end_absence` beendet nur Übersteuerungen mit passender `absence_id`. Eine während
der Abwesenheit gelöschte Zone fällt über `ondelete="CASCADE"` heraus, ohne die
Klammer für die übrigen Räume zu beschädigen.

## v0.8.2 -- die Anlage schaltete nichts: der Anlaufriegel ging nie mehr auf

Aus der echten Anlage gemeldet, nicht in einem Test gefunden: Schaltbefehle
scheiterten, die Oberflaeche zeigte dauerhaft "Scharf, Neustart fehlt", und nach einem
Neustart stand dort "Bereitschaft". Eine einzige Ursache erklaerte alle drei
Beobachtungen. `app.py`s Lifespan setzt den einmaligen, prozessweiten Riegel
`app.state.sending_allowed` aus `switching_allowed()` -- und die prueft seit dem
Aktiv-Bereitschafts-Verbund auch die Fuehrung. An dieser Stelle hat der Prozess den
Anspruch noch nie gestellt, die Schattenschleife startet erst danach; der Riegel war
also immer zu und wurde nur einmal je Prozess gelesen. Behoben mit
`control_armed_at_startup()` (`integrations/actuators.py`), die nur `control_armed`
liest. `switching_allowed()` selbst ist unveraendert: die Fuehrung wird weiterhin vor
jedem Sendevorgang geprueft, und die Schattenschleife ueberspringt ihren ganzen
Durchlauf, wenn sie nicht fuehrt -- eine Instanz in Bereitschaft schaltet nichts.
Zweitens stellt `_shadow_loop` den Anspruch jetzt vor dem ersten Schlafen statt danach,
in einem eigenen `try`, damit ein Datenbankfehler beim Start die Schleife nicht ohne
Wiederholungsversuch beendet.

**Der eigentliche Befund ist die Blindheit der Suite.** Sie baut ihr Schema ueber
`Base.metadata.create_all()`; die Migration `bb4a0ff63b2d` legt in jeder echten Anlage
eine `cluster_claim`-Zeile an, `create_all()` nicht, und `cluster.is_leader` faellt bei
fehlender Zeile bewusst offen aus. Dieselbe Funktion lieferte im Test `True` und in der
Anlage `False`. Die Fixtures wurden bewusst *nicht* pauschal umgestellt -- das haette
hunderte unbeteiligte Tests mit Verbund-Beiwerk zugestellt und dabei fail-open in
fail-closed verkehrt. Stattdessen ein gezielter Riegel gegen genau diese Kluft:
`tests/test_migrations.py::test_migrated_cluster_claim_does_not_freeze_the_startup_
bolt_closed` baut das Schema ueber `alembic upgrade head`, laeuft durch den echten
`_lifespan` und einen echten ersten Regelzyklus. **Wo eine Aussage von der Zeile
abhaengt, die nur die Migration legt, taugt ein `create_all()`-Test nicht als Beleg.**

Ausserdem: `CLAUDE.md` nannte das MariaDB-Testpasswort falsch (`prüfen` statt
`pruefen`, wie Container und CI es benutzen).

## v0.8.1 -- SQLite-Sperrfehler im Verbund-Anspruch behoben

CI schlug nach v0.8.0 zeitweise fehl: `tests/test_cluster.py::test_two_processes_
racing_for_a_stale_claim_only_one_wins` scheiterte auf einem langsamen Läufer mit
`OperationalError: database is locked` statt der erwarteten Niederlage
(`rowcount == 0`). `busy_timeout` allein behob es nicht (Python setzt ihn über den
Treiber ohnehin schon auf 5 s; `db/engine.py`s eigenes `PRAGMA busy_timeout=5000`
macht das nur ausdrücklich, ändert nichts). Ursache: `services/cluster.py::
try_become_leader` las vor dem Schreiben (Existenzprüfung, Datenbankzeit) --
unter SQLite hält eine solche Transaktion dabei nur eine SHARED-Sperre und muss
zum Schreiben auf RESERVED hochstufen. Zwei Prozesse, die beide erst lesen,
geraten beim gleichzeitigen Hochstufen in einen Fall, den SQLites Busy-Handler
grundsätzlich nicht auflöst (auch nicht mit `busy_timeout`) -- sofortiger Fehler
statt kurzer Wartezeit. Behoben, indem `try_become_leader` jetzt zuerst schreibt
und nur bei `rowcount == 0` nachträglich liest, um "verloren" von "Verbund nie
aktiviert" zu unterscheiden -- siehe die Funktion selbst und
`tests/test_cluster.py::test_try_become_leader_writes_before_it_reads_cluster_
claim`. Zusätzlich WAL-Journalmodus für dateibasierte SQLite-Datenbanken
(`db/engine.py`), mit Erkennung auch der URI-Speicherform
(`file::memory:?...&uri=true`).

**Bekannter, dokumentierter Vorbehalt, kein eigener Auftrag:** Dasselbe
Lese-vor-Schreiben-Muster steckt auch anderswo (z. B. `services/retention.py`,
siehe dortiger Kommentar) -- real, aber selten, mit einem einzelnen
fehlgeschlagenen Zyklus oder Zugriff als sichtbarer Folge, keinem stillen
Datenfehler. `try_become_leader` ist das Referenzmuster für eine Lösung, falls
das je zum echten Problem wird.

## Fenster-Erkennung aus einem Temperatursturz, ohne Kontakt

Eine Zone ohne zugeordneten Fensterkontakt kann jetzt trotzdem ein offenes Fenster
erkennen — an einem hinreichend steilen Abfall ihrer eigenen Raumtemperatur. Je Zone
einschaltbar (`Zone.window_temp_drop_detection_enabled`, Vorgabe **aus**), Regelparameter-
Seite der Zone. Ein zugeordneter Fensterkontakt hat immer Vorrang: `services/
ingest.py::_window_open` fragt zuerst, ob der Zone Fensterkontakt-Geräte zugeordnet sind
— ist das der Fall, entscheidet ausschließlich der Kontakt, auch während er gerade
unbekannt ist (stale/fehlend). Nur eine Zone ganz ohne Kontakt und mit eingeschaltetem
Schalter wird über die Temperatur beurteilt (`_window_open_from_temperature`).

**Kriterium** (`domain/window_temperature_drop.py::window_open_suspected`, im Aufbau
gespiegelt an `domain.fault.stuck_reading`): der Referenzwert im Verlauf der letzten
`setting.window_temp_drop_window_minutes` (Vorgabe 15) **vor** dem aktuellen Wert minus
dem aktuellen Wert erreicht `setting.window_temp_drop_threshold_k` (Vorgabe 1,5 K). Der
Schwellwert ist ein begründeter, aber ausdrücklich **nicht anlagenspezifischer**
Schätzwert — ohne Messdaten der echten Anlage lässt sich kein sicherer Wert herleiten;
deshalb bleibt die Erkennung je Zone standardmäßig aus, und der Betreiber schaltet sie
bewusst ein. Bewusst in Kauf genommene Fehlauslöser: eine geöffnete Tür, ein Luftzug, ein
ungünstig platzierter Sensor — keiner davon ist mit reiner Temperaturmessung von einem
echten Fensteröffnen zu unterscheiden. Ein langsames Auskühlen nach Heizende und das Ende
einer Heizphase selbst bleiben unterhalb der Schwelle und lösen nicht aus (mit Tests
belegt, nicht nur angenommen).

Jetzige Kennzahl: der **Referenzwert ist das Maximum der Vorwerte, es sei denn es gibt
mindestens drei — dann ist es deren zweithöchster Wert**. Mit weniger als drei Vorwerten
gibt es nichts, das sich gefahrlos verwerfen ließe, ohne genau die Werte zu verlieren,
die einen frühen Sturz überhaupt zeigen könnten — der Referenzwert bleibt dort ihr
Maximum. Bei `[20.00, 20.10, 18.60]` wird damit ein Sturz von 1,50 K erkannt.
Ab drei Vorwerten wird ein einzelner
verrauschter Ausreisser vom zweithöchsten Wert einfach überstimmt, während zwei oder
mehr echte Vorwerte auf dem tatsächlichen Sturzniveau den Vergleich weiterhin tragen —
ohne die Mehrheitsanforderung des Medians. **Bewusst bleibende Lücke, mit eigenem Test
festgehalten:** bei nur einem oder zwei Vorwerten (die kürzeste unterstützte
Fenstergröße, oder ein dünn besetztes Fenster nach einer Meldelücke) kann ein einzelner
Ausreisser weiterhin täuschen — das Schließen dieser Lücke würde genau die Daten
kosten, die ein kurzes Fenster nicht übrig hat. Mit eigenen Tests für das Reviewer-Beispiel, den
Ausreisser-Fall ab drei Vorwerten, die bewusst bleibende Lücke bei ein bis zwei
Vorwerten und eine Meldelücke mitten in der Messreihe belegt.

**Plausibilitätsgrenze:** Ein berechneter Sturz ab 6,0 K wird nicht als
Fensterereignis gewertet (`WINDOW_TEMP_DROP_MAX_PLAUSIBLE_DROP_K`). Auch der
zweithöchste Vorwert kann bei mehreren Ausreißern verfälscht sein, etwa bei
Vorwerten `[21.0, 30.0, 29.0]` und aktuellem Wert `21.0` (scheinbarer Sturz 8,0 K).
Die Grenze liegt über der höchsten einstellbaren Sturzschwelle von 5,0 K;
sie ist eine begründete Plausibilitätsannahme, keine Messung der echten Anlage.
Tests prüfen Mehrfachausreißer, den Grenzwert und den Abstand zur einstellbaren
Schwelle. Ausreißer unterhalb der Plausibilitätsgrenze bleiben möglich.

**Rücknahme.** Ein reines Sturzkriterium kennt kein Ende — ein bereits abgekühlter,
stabil kalter Raum zeigt keinen neuen Sturz mehr, obwohl das Fenster noch offen sein
könnte, und da die Erkennung selbst das Heizen abschaltet, gäbe es ohnehin keine aktive
Wärmequelle, die einen Temperaturanstieg als Entwarnungssignal liefern könnte — eine
Rücknahme über „die Temperatur steigt wieder" wäre also zirkulär. Gelöst über eine
gebundene Haltedauer (`temperature_detection_still_holding`,
`setting.window_temp_drop_hold_minutes`, Vorgabe 30): einmal ausgelöst, gilt die
Vermutung für diese Dauer weiter offen, auch ohne neuen Sturz, und fällt danach von
selbst wieder ab, sofern in der Zwischenzeit kein frischer Sturz sie erneuert. Dieselbe
Uhr wie beim echten Kontakt (`zone_state.window_open_since`) — kein zweiter Zeitstempel.

**Schutz gegen Rückkopplung:** Eine aufgrund der Vermutung abgeschaltete Heizung
kann weiteres Auskühlen und damit erneute Erkennung verursachen. Der Halt allein
begrenzt diese Folge nicht. Dagegen wirkt eine **kumulative** Zählung auf einer eigenen, von `window_open_since`
unabhängigen Uhr: `zone_state.window_temp_drop_streak_started_at` (seit wann die
Strähne läuft) und `zone_state.window_temp_drop_last_detected_at` (wann sie zuletzt
tatsächlich erkannt wurde). Ein frischer Sturz setzt die Strähne genau dann fort, statt
sie neu zu beginnen, wenn die Lücke seit der letzten Erkennung höchstens
`setting.window_temp_drop_gap_tolerance_minutes` (Vorgabe 10) beträgt
(`temperature_detection_gap_within_tolerance`) — kurz genug, um nur den einzelnen
verrauschten Ausreißer am Halt-Rand zu überbrücken, deutlich kürzer als der Halt selbst,
damit daraus kein zweiter Halt wird. Während der Halt selbst noch greift (`still_
holding`), braucht es diese Toleranzprüfung gar nicht — die Strähne läuft dort
ohnehin ununterbrochen weiter, bestätigt durch den Halt selbst, nicht nur vermutet aus
einer Lücke. Erreicht die Strähne `setting.window_temp_drop_max_suspected_minutes`
(Vorgabe 90, drei Halte), erzwingt die Erkennung eine **Zwangspause**: für
`setting.window_temp_drop_silence_minutes` (Vorgabe 60) bleibt die Zone geschlossen,
unabhängig von jedem weiteren Sturz (`temperature_detection_still_silenced`, eigene
Frist `zone_state.window_temp_drop_silence_until`). Danach darf die Erkennung wieder
auslösen, mit zurückgesetzter Strähne.

**Bewusst bleibende Lücke:** eine tolerierte kurze Unterbrechung ist nicht dasselbe wie
eine unbegrenzte — überschreitet die Lücke zwischen zwei erkannten Zyklen die Toleranz
tatsächlich (eine echte Erholung, oder eine längere Serie verrauschter Fehlschläge),
gilt die Strähne als beendet, und ein späteres erneutes Auslösen zählt wieder bei null.
Ein Raum, dessen Fehlauslöser zufällig weiter auseinanderliegen als die Toleranz, könnte
das im Prinzip unbegrenzt fortsetzen. Bewusst nicht geschlossen: eine deutlich längere
Toleranz würde beginnen, echte, voneinander unabhängige Episoden zu verschmelzen.

Mit eigenen Tests belegt: dem Reviewer-Szenario nachgestellt (ein einzelner Ausreißer
genau am Halt-Rand übersteht die Strähne unverändert), 20 simulierte Durchläufe über
420 Minuten mit demselben Ausreißer-Muster (die Zwangspause feuert; eine Gegenprobe mit
Toleranz null — dem alten, entfernten Verhalten entsprechend — feuert absichtlich nie),
sowie Obergrenze, Zwangspause während laufender Sturzverdachtsfälle und Ende der
Zwangspause wie zuvor.

Die bleibende Lücke ist in `tests/test_window_temperature_drop.py::
test_a_recovery_just_over_the_tolerance_lets_every_streak_restart` verankert:
20 Minuten Halt, danach 15 Minuten Erholung, 14 Wiederholungen über sieben Stunden.
Die Zwangspause greift dabei nie, während die Zone für zwei Drittel der Zeit als
offen gilt.

**Wirkt wie ein echter Kontakt.** `zone_state.window_open`/`window_open_since` werden für
beide Quellen identisch gesetzt; `domain/control_loop.py` (Fensterabschaltung,
Frostschutz-Ausnahme, EIN/AUS-Ausnahme, Wiederanlaufsperre) und
`domain/window_alarm.py` (Kälte-Alarm) bleiben deshalb **unverändert** — mit eigenem
Test belegt, dass Letzteres tatsächlich zutrifft, statt nur angenommen.

**Bleibt unterscheidbar, Grundsatz 5.** Neue Spalte `zone_state.
window_open_by_temperature`: `True` genau dann, wenn das aktuelle `window_open = true`
aus der Temperaturvermutung stammt, nie aus einem echten Kontakt. `domain/
control_loop.py::decide()` hängt jeder Begründung, bei der ein temperaturvermutetes
Fenster mitentscheidet, einen kurzen Zusatzsatz an („Fenster nicht gemessen, sondern aus
einem Temperatursturz vermutet."). Auf der Startseite ein eigener Status-Chip neben dem
bestehenden Fenster-Alarm-Chip.

**Nicht in REST, MCP oder Homebridge** — dieselbe, vom Projektinhaber vorgegebene Grenze
wie beim Fenster-Alarm. Die anlagenweiten Parameter liegen deshalb in einem eigenen
`WINDOW_TEMP_DROP_LIMITS` (`domain/control.py`), nicht im von REST und MCP mitbenutzten
`LIMITS`; der Zonen-Schalter ist keine `ControlParameters`-Spalte und hat eine eigene
kleine Speicherfunktion (`domain/zone_settings.py::set_window_temp_drop_detection`) samt
eigener Route (`/zones/{id}/window-temp-drop-detection`), damit er das Formular
`/zones/{id}/parameters` — das die REST-Antwort `ControlParametersResponse` verbatim
speist — nicht erreichen kann. In Home Assistant eine eigene, laufend gesendete
Diagnose-Entität je Zone (`state/window_open_by_temperature`), kein eigenes
Meldungssystem mit Zustellprotokoll wie beim Fenster-Alarm.

Migrationen: `e741133296d2`, `1b7bad26c13a` und `43aa18ba1c12`.

## Fenster: Frostschutz gewinnt, EIN/AUS-Aktoren schalten nicht ab

Zwei Entscheidungen des Projektinhabers an `domain/control_loop.py::decide()`.

**Frostschutz schlägt das offene Fenster.** Bisher schaltete Regel 3 bei offenem
Fenster bedingungslos ab — ein Raum konnte dabei tatsächlich unter den Frostschutz
fallen (Lüften vergessen, draußen kalt). Fällt die Zone trotz offenem Fenster unter
ihren Frostschutzwert, heizt sie jetzt wieder: Heizen gegen ein offenes Fenster ist
teuer, eingefrorene Leitungen sind teurer. Die Prüfung sitzt an Regel 3 selbst (vor
der bisherigen bedingungslosen Abschaltung), maßgeblich ist `frost_c`, nicht der
aufgelöste Sollwert, mit derselben Hysterese wie der Normalfall (kein Flattern am
Frostschutzwert) und weiterhin unter der Mindestschaltdauer aus Regel 5. Eigener
`reason_code`: `frostschutz_trotz_fenster_offen`.

**EIN/AUS-Aktoren (Fußbodenheizung an reinen Ein/Aus-Ventilen) schaltet das Fenster
nicht mehr ab** — zu träge, als dass ein Abschalten beim Lüften etwas brächte.
Erkennungsmerkmal: die Zone hat Aktoren, und keiner davon ist
`Device.self_regulating` (`Situation.on_off_actuators_only`, hergeleitet in
`services/shadow_run.py::_on_off_actuators_only`). Gemischte Zonen (mindestens ein
selbstregelndes Ventil) bleiben beim bisherigen, vorsichtigeren Verhalten. Nur das
Abschalten entfällt — Fenstererkennung, -protokollierung und der spätere
Kälte-Alarm sind unverändert. Rule 4 (Wiederanlaufsperre) ist für solche Zonen aus
demselben Grund mit ausgenommen: sie hat nie abgeschaltet, es gibt nichts, wovon sie
sich erholen müsste.

**PI-Zweig mitgezogen:** Jede PI-fähige Zone hat per Definition nur gewöhnliche
(nicht-selbstregelnde) Schaltaktoren — genau die Bedingung für
`on_off_actuators_only`. Ein offenes Fenster gated PI deshalb nur noch für
gemischte Zonen; für eine reine EIN/AUS-Zone läuft PI unverändert weiter, als gäbe
es kein Fenster (`services/shadow_run.py::_pi_gate_reason`, neuer Parameter
`window_governs`). Beim Bau fiel dabei ein zweiter, unabhängiger Fehler auf:
`_pi_outcome`s `resume_delay_active` berechnete rule 4s Bedingung nochmal selbst,
ohne von der EIN/AUS-Ausnahme zu wissen — behoben in derselben Änderung.

`shadow_decision.reason` und `.setpoint_reason` sind `Text` statt `String(255)`
(Migration `c1a4e9d872b3`), damit kombinierte Begründungen auch unter MariaDB Platz
haben. Der Hinweistext `on_off_zone_note` bleibt kurz.

Getestet in `tests/test_control_loop.py` (Schwellwert, Hysterese ohne Flattern,
Mindestschaltdauer, EIN/AUS- vs. gemischte Zone, Zusammenspiel beider Änderungen),
`tests/test_control_loop_state_table.py` (die vollständige Zustandstabelle, um die
Vorrangkette weiterhin erschöpfend zu beweisen) und `tests/test_shadow_run_pi.py`
(`_pi_gate_reason`-Klassifikation, End-zu-Ende gegen eine echte Zone, je eine
gemischte und eine reine EIN/AUS-Zone).

`already_engaged` verlangt neben `heating_now` auch
`not situation.valve_protection_active`: ein unterbrochener Ventilschutzlauf wird
nicht als bereits aktive Frostschutz-Ausnahme protokolliert. Geprüft durch
`test_frost_override_is_not_attributed_to_an_interrupted_protection_run` und die
Zustandstabelle. `tests/test_shadow_run.py` prüft `_on_off_actuators_only()` für
Zonen ohne Aktor, gemischte Zonen und mehrere reine EIN/AUS-Aktoren.

## Urlaubsbetrieb: Absenkung deckelt nicht mehr unter den Frostschutz einer Zone

Review-Befund, sicherheitsrelevant nach Grundsatz 7: `_vacation_setpoint()` gab den
eingegebenen Absenkwert bislang ungeprüft zurück. Zone mit Frostschutz 16,0 °C,
Urlaub mit 5,0 °C, Betriebsart `auto` → geregelt wurde auf 5,0 °C. Behoben in
`domain/schedule.py::_vacation_setpoint()`: der Absenkwert wird jetzt je Zone auf
`max(setback_temperature_c, Frostschutz dieser Zone)` gedeckelt — derselbe absolute
Frostschutz-Boden, den `domain/solar_setback.py::apply()` für seine eigene Korrektur
schon durchsetzt. Greift der Frostschutz, lautet die Begründung im `Setpoint`
„Urlaubsbetrieb — Absenkung durch Frostschutz angehoben" statt der bisherigen, dann
unehrlichen „Urlaubsbetrieb — Absenkung" (Grundsatz 5). `schedule_forecast()` heilt
dadurch mit, da es denselben Weg über `resolved_setpoint()` nimmt — mit eigenem Test
bestätigt statt nur angenommen. `docs/api.md` zieht die Grenze bei
`setback_temperature_c` entsprechend nach.

## Urlaubsbetrieb: anlagenweite Absenkung über ein festes Zeitfenster

Tabelle `vacation` (Migration `4bfefd4c10a4`): ein einziger
Absenkwert für die ganze Anlage über ein Zeitfenster mit fest eingegebenem Beginn
und Ende, danach läuft der Zeitplan von selbst weiter. Umgesetzt als eigener
Zustand, den `domain/schedule.py::resolved_setpoint()` direkt abfragt — nicht als
je-Zone angelegte `ZoneOverride`-Zeilen (die verworfene Alternative): Eine später
angelegte Zone wäre sonst nicht erfasst, und das Aufheben einer einzelnen
Override-Zeile hätte diese Zone stillschweigend aus dem Urlaub herausgenommen,
ohne dass irgendwo stünde, dass das passiert ist.

Vorrangkette in `resolved_setpoint()`: Betriebsart „Aus" > laufende, von Hand
gesetzte Zonen-Übersteuerung > Urlaub > Zeitplan > Frostschutz. Eine laufende
Übersteuerung geht beim Start eines Urlaubs also nicht verloren; eine Zone auf
„Aus" bleibt aus. `control_loop.decide()` selbst ist unverändert — Fenster,
Sensorausfall, Mindestschaltdauern und Ventilschutz laufen unwissend vom Urlaub
genau wie bei jeder Übersteuerung. `schedule_forecast()` zieht Beginn und Ende
des Urlaubs als zusätzliche Balkengrenzen neben dem nächsten Zeitplan-Schaltpunkt,
damit die 24-Stunden-Vorschau einen mitten in ihr beginnenden oder endenden
Urlaub nicht in einen falsch breiten Zeitplan-Balken auflöst.

Ein- und Ausgabe lokal, Speicherung über `domain/time.py::local_day_start_utc` in
UTC — DST-sicher, dieselbe Umrechnung wie Statistik und Audit-Log. Beginn und Ende
sind volle lokale Kalendertage, beide eingeschlossen. Eigenes Recht
`vacation.manage` statt `setting.manage` oder dem zonenbezogenen
`override.create` — Begründung in `db/models/lookup.py`. Oberfläche: eigene Seite
`/vacation` (Lesen `zone.read`, Ändern `vacation.manage`), zusätzlich ein Hinweis
auf der Startseite, sichtbar sowohl während ein Urlaub läuft als auch, solange
er erst geplant ist. REST (`GET`/`POST`/`DELETE /api/v1/vacation`) und MCP
(`read_vacation`, `vacation`, `cancel_vacation`) bekommen die Funktion
gleichermaßen — Grundsatz 6.

`services/shadow_run.py`: die PI-Sollwertkontext-Funktion
(`_pi_setpoint_context_key`) kannte bislang nur Zeitplan-Modus oder Zonen-
Übersteuerung als Ursprung eines Sollwerts; ein Urlaub liefert wie eine feste
Übersteuerung `mode_id=None`, ohne eine Übersteuerungs-Zeile zu sein. Ohne eigenen
Zweig hätte das den dortigen `assert` verletzt, sobald ein Urlaub auf einer
PI-aktivierten Zone ohne laufende Übersteuerung greift — gefunden beim Nachvoll-
ziehen der Vorrangkette, PI selbst hat noch keinen scharfen Betriebspfad.

## Außentemperatur und Fenster-Alarm

Zwei neue, zusammengehörige Stücke:

**Außentemperatur.** `setting.outdoor_temperature_source_device_id` -- genau eine
Quelle für die ganze Anlage, ausgewählt aus den bekannten Zigbee2MQTT-Geräten wie
`zone.temperature_source_device_id` für eine Zone (`domain/outdoor.py`, Auswahl
unter `/settings`). Alter und Ausfall werden wie bei einem Zonensensor beurteilt
(`domain.fault.sensor_state`, gegen `setting.default_sensor_timeout_seconds`) --
"keine Quelle gewählt" und "Wert veraltet" sind eigene Texte, keine Zahl, die man
mit einem tatsächlichen Messwert verwechseln könnte. Sichtbar auf der Startseite
und per MQTT als eigene, anlagenweite Home-Assistant-Entität (`sensor`,
`aussentemperatur`).

**Fenster-Alarm.** Eine fünfte, eigene Meldungsart (`notify_window_alarm`) neben
den bestehenden vier -- meldet, wenn ein Fenster einer Zone länger als
`setting.window_alarm_open_minutes` (Vorgabe 30) offen steht **und** die
Außentemperatur unter `setting.window_alarm_outdoor_threshold_c` (Vorgabe 5,0 °C)
liegt. Beide Bedingungen streng (`domain.window_alarm.window_alarm_state`):
"seit mehr als", nicht "seit mindestens"; "unter", nicht "höchstens". Fehlt die
Außentemperatur oder ist sie veraltet, ist das Ergebnis `None` -- ein
unbekannter Zustand, der nie als Alarm **oder** als Entwarnung gilt
(`zone_state.window_alarm`, tri-state). Gemeldet wird, wie beim festhängenden
Messwert, nur der Übergang, samt Entwarnung (`domain.fault_notice.
window_alarm_notice`) -- über eine eigene, von der Sensorstörung getrennte
Home-Assistant-Entität je Zone (`fenster:<id>`, nicht `sensor:<id>`: die beiden
Zustände können gleichzeitig gelten und dürfen sich nie überschreiben).

**Bewusst nicht in REST, MCP oder Homebridge.** Ausdrückliche Vorgabe des
Projektinhabers: Neues zu Fenster und Außenwert erscheint nur in der
Oberfläche und in Home Assistant. Die beiden Fenster-Alarm-Schwellen liegen
deshalb in einem eigenen `WINDOW_ALARM_LIMITS` (`domain/control.py`), nicht in
dem von REST und MCP mitbenutzten `LIMITS` -- eine eigene, kleine
Validierungsfunktion (`check_number`, jetzt parametrisiert) statt eines
gemeinsamen Wertebereichs, der die beiden Schwellen versehentlich mit
hinausgetragen hätte.

Migration `f18d4dcb3f5d`. Der Alarm ist eine Meldung, kein Eingriff in die Regelung.
Die Fensterabschaltung ist oben separat beschrieben.

## Migrationssperre: gleichzeitige `alembic upgrade head`-Läufe abgesichert

`migrations/env.py` nimmt jetzt vor jedem Migrationslauf eine Datenbank-Sperre
(neu: `thermoctl/db/migration_lock.py`) — wirkt für **jeden** Alembic-Aufruf,
auch von Hand, nicht nur beim Containerstart, weil sie dort und nicht im
Entrypoint sitzt. MariaDB und SQLite bekommen bewusst unterschiedliche, aber
je backend-eigene Mittel statt eines erzwungenen gemeinsamen: `GET_LOCK()`
(MariaDB, session-gebunden, mit eigenem Timeout) bzw. eine `flock`-Dateisperre
neben der Datenbankdatei (SQLite; eine In-Memory-Datenbank braucht keine, sie
gehört ohnehin nur einem Prozess). Beide lösen sich beim Absturz des Halters
von selbst — kein Risiko, dass eine tote Sperre die Anlage stilllegt
(Grundsatz 7). Wer die Sperre nicht bekommt, wartet bis zu
`THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS` (Vorgabe 60s) und bricht dann mit
einer klaren Fehlermeldung ab, statt endlos zu hängen; der Normalfall (unbesetzte
Sperre) kostet nichts Messbares. Die vorgeschalteten Einmal-Jobs in
`docs/docker-swarm.md` und `docs/kubernetes.md` sind dadurch für die
Absicherung selbst entbehrlich geworden (Anleitungen entsprechend angepasst),
bleiben aber als Option für alle, die den Migrationsschritt trotzdem sichtbar
getrennt vom Ausrollen des Dienstes sehen wollen.

Nachgewiesen in `tests/test_migrations.py`
(`test_two_concurrent_upgrade_head_runs_do_not_collide`, zwei echte
`alembic`-Unterprozesse gegen MariaDB, per Barriere gleichzeitig losgelassen,
und `test_upgrade_head_against_an_already_current_database_is_a_quick_no_op`)
und in `tests/test_migration_lock.py` (Erwerb, Freigabe, Warten mit
Zeitüberschreitung, Absturz des Halters — je gegen SQLite **und** MariaDB,
unabhängig vom Backend des jeweiligen Testlaufs).

## Betrieb unter Docker Swarm und Kubernetes dokumentiert

Zwei Anleitungen für den Aktiv-Bereitschafts-Verbund:
[`docs/docker-swarm.md`](docker-swarm.md) und [`docs/kubernetes.md`](kubernetes.md), je
mit lauffähigen Beispieldateien (`docker/swarm.compose.beispiel.yml`,
`docker/swarm.migrate.compose.beispiel.yml`, `k8s/*.beispiel.yaml`). Zwei Dateien statt
einer gemeinsamen, weil die Beispielmanifeste beider Systeme sonst dieselbe Anleitung mit
zwei unvereinbaren YAML-Dialekten überladen hätten.

## Erkennung eines festhängenden Messwerts

`domain/fault.py::sensor_state` prüfte bisher nur das Alter der letzten Meldung — ein
Sensor, der zuverlässig alle paar Minuten dieselbe Zahl schickt, galt dauerhaft als
`ok`. Neu: `stuck_reading()` vergleicht die Spanne aller Messwerte der letzten
`stuck_reading_hours` Stunden (Vorgabe 12, anlagenweit) gegen eine kleine Schwelle
(0,05 °C) — nicht bitgleiche Werte, sonst zählte ein Sensor, der zwischen zwei
Auflösungsschritten pendelt (22,7/22,8 °C), fälschlich als festhängend. Ist die
Historie kürzer als die Schwelle, gibt es keinen Verdacht, keinen Fehlalarm.

**Entscheidung des Projektinhabers: melden, aber weiterregeln.** `zone_state.sensor_stuck`
ist von `sensor_status_id` und damit von `decide()` in `domain/control_loop.py` komplett
unabhängig — die Regelentscheidung ändert sich nicht, eine Zone fällt deswegen nicht in
den Frostschutz. Nur berechnet, solange der Sensor sonst `ok` ist (eine ausgefallene
oder fehlende Quelle hat ihre eigene, unveränderte Erkennung). Eine vierte, eigene
Meldungsart (`notify_stuck_sensor`) statt ein Zusatz zur Sensorstörung — beide schließen
sich gegenseitig aus und ein gemeinsamer Schalter würde eine harmlose, oft tagelange
Beobachtung mit einem echten Sensorausfall verkoppeln. Sichtbar auf Startseite und
Kiosk, sowie über `sensor_stuck` in REST- und MCP-Zonenzustand. Migration `afb9832fba99`,
Kopf danach unverändert einzügig.

## Zeitplan-Vorschau: nächste 24 Stunden je Zone

Die Zeitplanseite (`/zones/{id}/schedule`) zeigt jetzt oberhalb des Wochenrasters eine
Leiste mit der Vorschau der nächsten 24 Stunden, sichtbar bereits mit `zone.read`
(keine Änderungsrechte nötig). Die Berechnung sitzt in der Domäne
(`thermoctl.domain.schedule.schedule_forecast`), nicht in der Ansicht: sie reicht
über `resolved_setpoint`s eigene Rangfolge (Betriebsart Aus schlägt alles, dann eine
laufende Übersteuerung bis zu ihrem Ende, dann Urlaub, Zeitplan und zuletzt Frostschutz) und
kann daher nie etwas zeigen, was zur Laufzeit nicht tatsächlich einträte. Sie rechnet
in echten UTC-Instanzen statt in Ortszeit-Arithmetik und bleibt deshalb auch über
Mitternacht, einen Wochentagswechsel und beide Sommerzeit-Umstellungen (23- bzw.
25-Stunden-Tag) exakt — mit Tests, die diese Grenzfälle einzeln mit von Hand
abgeleiteten Uhrzeiten belegen (`tests/test_domain_schedule.py`). REST und MCP bieten
die Vorschau noch nicht an; das ist eine bewusste Auslassung dieser Aufgabe, keine
technische Grenze — die Domänenfunktion ist adapterunabhängig nutzbar.

## Liveaktualisierung der Startseite

Ist-Wert, Sollwert samt Begründung, Sensorzustand und letzte Entscheidung je Zone
zeigten sich bisher nur beim Neuladen. `start.html` fragt die Startseite jetzt im
Stil des Kiosks periodisch selbst ab (`hx-get`/`hx-select`/`hx-swap="outerHTML"` auf
`#tc-live`) — kein neues Werkzeug, HTMX war schon da.

Das Intervall ist keine feste Zahl, sondern kommt aus `setting.shadow_interval_seconds`
(`poll_interval_seconds` in `start_views.py`) — häufiger abzufragen als sich ein Wert
ändern kann, wäre verschwendete Last, gerade hinter dem Ingress-Proxy. `[!document.hidden]`
im `hx-trigger` pausiert den Abruf, solange der Tab im Hintergrund liegt.

Zwei Dinge mussten dafür ausdrücklich abgesichert werden, nicht nur der Abruf selbst:

- Ein aufgeklappter Übersteuern-Bereich und eine schon begonnene Eingabe dürfen den
  Austausch nicht verlieren. Gelöst über `hx-preserve` auf dem Formular und dem
  Auf/Zu-Knopf (beide mit stabiler `id`) — htmx lässt diese Elemente beim Swap
  unangetastet stehen, statt sie durch eine frische, leere Fassung zu ersetzen.
- Der globale Ladebalken (`loading_indicator.js`) darf für diesen Abruf nicht
  aufblitzen — er würde jede Minute von selbst erscheinen, ohne dass irgendjemand auf
  ihn wartet. Das auslösende Element trägt `data-tc-quiet-poll`; das Skript prüft
  genau dieses Element (`ereignis.detail.elt`), nicht dessen Vorfahren, damit ein POST
  aus einem Formular *innerhalb* des Bereichs (Übersteuern, Sollwert stellen) den
  Balken weiterhin zeigt.

Nachgewiesen in `browser_tests/test_start_page_live.py` (vier Tests: abgeleitetes
Intervall, ein geänderter Wert aktualisiert sich ohne Zutun, ein aufgeklappter Bereich
samt begonnener Eingabe übersteht eine Aktualisierung, der Ladebalken bleibt dabei
stumm — auch unter einer künstlich verzögerten Antwort).

## Aktiv-Bereitschafts-Verbund: zwei Instanzen, eine Datenbank, ein Broker

Zwei `thermoctl`-Instanzen können jetzt dieselbe Datenbank und denselben MQTT-Broker
teilen — eine regelt, die andere steht bereit und übernimmt, wenn die erste ausfällt.
Neu: `thermoctl/services/cluster.py` (die einzige Stelle, die die Tabelle
`cluster_claim` liest oder schreibt), Migration `bb4a0ff63b2d` (legt die Tabelle an,
mit einer unbeanspruchten Seed-Zeile, und `setting.cluster_takeover_cycles`, Vorgabe 5).

**Der Anspruch wechselt durch eine einzige atomare `UPDATE`-Anweisung, nicht durch
Lesen-Prüfen-Schreiben.** Zwei Instanzen, die gleichzeitig um einen abgelaufenen
Anspruch konkurrieren, stellen beide dieselbe Anweisung — die Zeilensperre der
Datenbank serialisiert sie, und nur wer als Erster drankommt, sieht seine
`WHERE`-Bedingung noch zutreffen; der Verlierer betrifft null Zeilen und misslingt
dadurch, nicht durch eine zusätzliche Prüfung. `tests/test_cluster.py::
test_two_processes_racing_for_a_stale_claim_only_one_wins` lässt zwei Threads mit
zwei echten, unabhängigen Datenbankverbindungen über eine `threading.Barrier`
tatsächlich gleichzeitig antreten — nicht zwei Aufrufe nacheinander, die auch eine
falsche Umsetzung bestehen würde. Läuft gegen SQLite **und** MariaDB grün; unter
MariaDB ist es der Ernstfall (`REPEATABLE READ` verhält sich unter echter
Nebenläufigkeit anders als SQLites Dateisperre).

**Die Uhr, die zählt, ist die der Datenbank, nie die eines Hosts.** Jeder Vergleich
und jede Erneuerung von `expires_at` geht über `sqlalchemy.func.now()` — ausgewertet
von der Datenbank selbst. Zwei Maschinen stimmen ihre Uhren nie so genau ab, dass sie
sicher entscheiden könnten, wer einen Heizkörper schalten darf; die Datenbank, die
beide ohnehin teilen, ist die eine Uhr, auf die sich beide ohne Weiteres einigen.

**Ein fehlender Anspruch-Datensatz heisst „Verbund nie eingerichtet"** und lässt jeden
Aufrufer als Alleinbetreiber gelten (`cluster.is_leader`/`try_become_leader`, beide
mit derselben Begründung dokumentiert). Genau dieser Fall gilt für die gesamte
Testsuite: sie baut ihr Schema über `Base.metadata.create_all()`, nicht über die
Migration, die die Seed-Zeile anlegt — ohne diesen bewussten Fehlschlag-offen-Fall
hätte jeder bestehende Schalttest im Projekt einen Anspruch-Datensatz gebraucht, von
dem er nie etwas wusste.

**Drei Riegel jetzt, nicht mehr zwei**, alle an derselben Stelle
(`integrations/actuators.py::switching_allowed`): `setting.control_armed`, der beim
Prozessstart eingefrorene Bolzen (`MqttClient`/`meross_switching_allowed`), und jetzt
`cluster.is_leader` — geprüft bei jedem einzelnen Schaltversuch, nicht nur einmal am
Zyklusanfang, damit eine Instanz, die ihren Anspruch mitten in einem langen Zyklus
verliert, den nächsten Aktor trotzdem nicht mehr anfasst. Erreicht damit sowohl den
Zigbee2MQTT- als auch den Meross-Pfad, weil beide durch dieselbe Funktion gehen.

**Der Regelzyklus selbst läuft auf der Bereitschaft gar nicht erst**
(`app.py::_shadow_loop`): jeder Durchlauf versucht zuerst atomar, den Anspruch zu
werden oder zu erneuern; nur wer gerade führt, macht mit dem restlichen Durchlauf
weiter (Sensorzustand, Schattenentscheidungen, Veröffentlichung, Meross-Abgleich,
Aufbewahrung). Eine Bereitschaft schreibt dadurch weder Schattenentscheidungen noch
Zustände an Home Assistant — zwei Instanzen, die dieselben zurückbehaltenen
Zustands-Topics beschreiben, wäre genau die Unschönheit, die dieses Verhalten
vermeidet (die MQTT-Client-Kennung muss je Instanz ohnehin verschieden sein, siehe
`docs/self-hosting.md`). Ein eingehender MQTT-Befehl (z. B. ein Moduswechsel aus Home
Assistant) darf auf beiden Instanzen angewendet werden — eine Konfigurationsschreibung,
kein Schaltvorgang, und ohnehin deckungsgleich, egal welche Instanz sie ausführt —,
aber nur die führende bestätigt ihn zurück an Home Assistant.

**Ein geordnetes Herunterfahren gibt den Anspruch sofort frei**
(`cluster.release`, aufgerufen aus `_lifespan`s `finally`), statt die
Bereitschaft die vollen fünf Zyklen warten zu lassen — der häufigste Fall ist ein
Neustart der aktiven Instanz, und die weiss beim Beenden bereits, dass sie geht.

**Sichtbar gemacht:** Die Startseite zeigt einen eigenen "Bereitschaft"-Chip, sobald
diese Instanz nicht führt — unabhängig vom sonstigen scharf/Trockenlauf-Zustand.

**Bewusst nicht gebaut:** Der Bereitschafts-MQTT-Client bleibt verbunden und nimmt
weiter Messwerte auf (beide Instanzen müssen ihre Datenbank aktuell halten, damit,
wer übernimmt, sofort mit frischen Daten arbeitet) — nur die Rückbestätigung eines
Befehls und jede Veröffentlichung sind an die Führungsrolle gebunden. Eine
gleichzeitige Bearbeitung derselben eingehenden Bruecken-/Störungsmeldung durch beide
Instanzen (mit je einem eigenen Webhook-Versand) ist ein bekannter, dokumentierter
Grenzfall und nicht gelöst — selten genug (die Brücke fällt nicht oft aus), dass er
bewusst zurückgestellt wurde, statt die Befehlsverarbeitung selbst zu verkomplizieren.

Details, inklusive Einrichtung je Instanz, in `docs/self-hosting.md`, Abschnitt 6d.

## Zwei Fehler in der Homebridge-Konfiguration behoben

Aus dem echten Betrieb gemeldet: ein Wechsel von Aus auf Automatik kam bei thermoctl nie
an, und eine Zone ohne Messwert liess HomeKit mit `... number 0 exceeded minimum of 10`
abstürzen. Beide Ursachen lagen in der Beispielkonfiguration für `mqtt-thing`
(`thermoctl/domain/interfaces.py::homebridge_zone_configs`, `docs/homebridge.md`), nicht
im MQTT-Vertrag selbst:

- Die bisherigen `apply`-Funktionen für Ziel-Zustand rechneten mit HomeKit-Zahlen
  (0/1/2/3) — aber `mqtt-thing`s `multiCharacteristic` übergibt beim Setzen bereits den
  **Listenwert** aus `heatingCoolingStateValues` an `apply`, nicht die Zahl, und schlägt
  beim Lesen `apply`s Rückgabe in derselben Liste nach. Die Zuordnung stand deshalb auf
  keiner Seite je richtig. Behoben, indem `heatingCoolingStateValues` jetzt direkt
  thermoctls eigenes Vokabular trägt (`off`/`manual`/`auto`); Ziel-Zustand braucht dadurch
  gar kein `apply` mehr. Der Ist-Zustand (`would_heat`, `true`/`false`) bleibt die eine
  Ausnahme mit eigenem `apply` — und das ruft jetzt `message.toString()` auf, weil
  `apply` den rohen MQTT-`Buffer` bekommt, gegen den ein bloßes `=== 'true'` nie zutrifft.
- Eine Zone ohne Messwert veröffentlicht bewusst eine leere Nutzlast
  (`_as_text(None)`) — für Home Assistant richtig (dessen MQTT-Climate-Integration
  ignoriert eine leere Nutzlast ausdrücklich), aber `mqtt-thing`s Fliesskommaparser macht
  daraus `NaN`, was HomeKit unterhalb von `minTemperature` ablehnt. `publishing.py` bleibt
  deshalb unverändert; `getCurrentTemperature` bekommt stattdessen ein `apply`, das bei
  leerer Nutzlast `undefined` liefert.

Beide Befunde sind gegen den Quelltext des Plugins geprüft (dessen index.js und
libs/mqttlib.js, `arachnetech/homebridge-mqttthing`), nicht nur gegen dessen README. Vier neue
Wächtertests in `tests/test_homebridge_interface.py` prüfen jetzt zusätzlich, dass Ziel-
Zustand kein `apply` mehr trägt, dass `heatingCoolingStateValues` thermoctls Vokabular an
den richtigen Indizes führt und Index 2 keinen gültigen Modus ist, dass der Ist-Zustands-
`apply` in dieselbe Liste decodiert und `.toString()` aufruft, und dass der Temperatur-
`apply` eine leere Nutzlast auf `undefined` abbildet — die bisherigen Tests prüften nur die
Topic-*Pfade*, nie den Inhalt der `apply`-Funktionen oder `heatingCoolingStateValues`, und
hätten diese Fehlerklasse deshalb nicht gefunden.

## Passkeys und MCP-Token haben jetzt eigene Add-on-Felder

`docker/thermoctl_optionen.py`: `mcp_token`, `passkey_rp_id`, `passkey_rp_name` und
`passkey_origin` sind aus `BEWUSST_AUSGELASSEN` in `ABGEBILDETE_FELDER` gewandert —
bisher nur über das freie `env`-Feld erreichbar, jetzt mit eigener Beschriftung im
Add-on-UI. `tools/env_nach_addon.py::_SCHEMA_REIHENFOLGE` musste dieselben vier
Felder bekommen, sonst hätte `als_yaml` sie beim Umstieg von `.env` auf das Add-on
stillschweigend verworfen, statt sie auszugeben — ein neuer Test
(`test_jedes_dedizierte_feld_steht_in_der_schema_reihenfolge`) hält beide Listen
seither zusammen.

**Befund zu Passkeys hinter Ingress:** Sie funktionieren, solange Home Assistant
selbst unter einem Hostnamen erreichbar ist — `passkey_rp_id`/`passkey_origin` müssen
dann auf *diesen* Hostnamen zeigen, nicht auf einen des thermoctl-Containers, den der
Browser unter Ingress nie sieht (Home Assistant Core reicht die Anfrage intern
weiter, `X-Ingress-Path` gesetzt, siehe Abschnitt oben zum Ingress-Präfix). Bei
reinem IP-Zugriff auf Home Assistant (kein Hostname) funktionieren Passkeys
grundsätzlich nicht — WebAuthn verlangt einen gültigen Domainnamen als
Relying-Party-Id, keine Einstellung kann das umgehen. Details in
`docs/self-hosting.md`, Abschnitte 6c und 8.

## Der Ingress-Präfix gilt jetzt pro Anfrage, nicht mehr pro Prozess

Als Home-Assistant-Add-on ist `thermoctl` sowohl über Ingress als auch — der
Container-Port ist freigegeben (`ports: 8000/tcp`) — direkt über einen eigenen
Reverse-Proxy erreichbar, beides gleichzeitig, aus demselben laufenden Prozess.
`thermoctl/app.py::create_app` entscheidet den Ingress-/Reverse-Proxy-Präfix dafür
pro Anfrage (Middleware `resolve_root_path`), nicht mehr einmal für den ganzen
Prozess über FastAPIs `root_path`-Konstruktorargument: Trägt die Anfrage die
Kopfzeile `X-Ingress-Path` mit exakt dem Wert, den dieser Prozess beim Start vom
Supervisor erfragt hat (`Settings.root_path`, gesetzt über
`docker/thermoctl_ingress.py`), gilt der Präfix für diese Anfrage — sonst nicht,
unabhängig davon, was konfiguriert ist (`thermoctl.app._ingress_header_prefix`).
Ohne konfigurierten Präfix wird die Kopfzeile gar nicht erst gelesen. Recherchiert
(Quelltext von `home-assistant/core`,
`homeassistant/components/hassio/ingress.py::_init_header`): Home Assistant Core
setzt diese Kopfzeile unbedingt auf jeder über Ingress weitergeleiteten Anfrage,
HTTP wie WebSocket, mit exakt dem Wert, den der Supervisor als `ingress_entry`
ausgibt — beide stimmen byteweise überein, wenn eine Anfrage tatsächlich über
Ingress kam. Fehlt die Kopfzeile trotzdem, wird die Anfrage wie eine direkte
behandelt: sichtbar unpräfigierte Navigation statt eines stillen
Sicherheitsproblems.

Sitzungscookies folgen demselben Präfix (`thermoctl/web/urls.py::cookie_path`) und
sind dadurch ebenfalls pro Zugangsweg getrennt, solange beide unter
unterschiedlichen Adressen erreichbar sind — der übliche Fall. Details, auch zum
Randfall gleicher Hostname/unterschiedlicher Port, und zum Befund bei Passkeys (an
den einen konfigurierten Hostnamen gebunden, nur untersucht, nicht gelöst) stehen in
`docs/self-hosting.md`, Abschnitt 6c.

## Eine per Boost ausgelöste Übersteuerung liess sich nicht aufheben

Boost gibt es an drei Stellen (Kiosk, MQTT/Home Assistant, REST/MCP); aufheben liess sich
zuvor nur an einer, der Startseite der angemeldeten Oberfläche. REST und MCP hatten
`cancel_override` bereits. Ergänzt: ein Kiosk-Knopf „Übersteuerung aufheben" (nur
sichtbar, wenn eine Übersteuerung läuft; Recht `override.cancel`, nicht `override.create`
-- er hebt womöglich die Übersteuerung eines anderen auf) und für Home Assistant der
Befehl `command/cancel_override` samt Discovery-Knopf sowie der neue Zustandswert
`state/override_active`, damit sichtbar ist, ob es dort überhaupt etwas aufzuheben gibt.
`domain/schedule.py::running_override` ist die neue, einmal implementierte Abfrage dafür.
Damit ein Kiosk-Token diesen Knopf je sehen kann, gehört `override.cancel` jetzt zu
`domain/kiosk.py::KIOSK_CONTROL_PERMISSIONS` -- ohne das hätte `issue_kiosk_token`
niemals ein Token damit ausgestattet, und der Knopf wäre für jedes Wandtablett
unerreichbar geblieben. Neu ausgestellte Kiosk-Token bekommen das Recht automatisch;
schon ausgestellte brauchen dafür eine erneute Ausstellung.

## Homebridge-Konfiguration direkt auf der Schnittstellen-Seite

`/interfaces` zeigt je sichtbarer Zone einen fertigen `mqtt-thing`-Block zum Kopieren.
Die Topics baut `domain/interfaces.py` über dieselben Funktionen wie die
Veröffentlichung selbst (`states_topics`/`command_topics`), mit dem tatsächlich
konfigurierten MQTT-Präfix — kein zweites Mal abgeschrieben, und ein Wächtertest hält
das gerenderte HTML gegen `publication.py`.

**Zugangsdaten stehen nie darin.** Homebridge bekommt Platzhalter und den Hinweis, dass
es einen eigenen Broker-Zugang mit engen Rechten braucht; die Erzeugung liest die
eigenen MQTT-Zugangsdaten gar nicht erst.

Die Seite verlangt weiterhin `setting.manage`, der Abschnitt zeigt aber nur Zonen, für
die zusätzlich `zone.read` vorliegt — dieselbe Filterung, mit der die Bediengeräteseite
ihren Befund von 2026-09-02 behoben hat.

Der Kopierknopf kommt ohne Bibliothek aus. Die Zwischenablage-Schnittstelle des Browsers
verlangt einen sicheren Kontext, den ein Heimnetz über schlichtes HTTP nicht bietet;
dort greift ein Rückfallweg, und scheitert auch der, sagt der Knopf es, statt stumm zu
bleiben.

## Wo das Projekt steht

Die Anlage des Projektinhabers läuft seit dem 2026-09-02 scharf mit `thermoctl`, das
Altsystem bleibt parallel als Rückfallebene. Das Repository ist seit `v0.6.1` öffentlich
(`github.com/MagicalWig34653/thermoctl`), unter der AGPL-3.0. Der Betrieb läuft heute in
zwei Formen: als eigener Docker-Container (`docker compose`) und als
Home-Assistant-Add-on — Letzteres war im ursprünglichen Rahmenentwurf nicht vorgesehen,
siehe [roadmap.md](roadmap.md).

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | gebaut; die Abnahme anhand echter Betriebsdaten ist geprüft und **nicht bestanden** (siehe unten) |
| 3 — Konfigurations-Oberfläche | abgeschlossen |
| 4 — Regelkreis und Cutover | schaltet scharf an der echten Anlage; Ablösung des Altsystems noch offen |
| 5 — Integrationen und Veröffentlichung | Repository öffentlich, Add-on-Betrieb dazugekommen |

Details je Phase, Aufgabenlisten und was nicht ursprünglich vorgesehen war stehen in
[roadmap.md](roadmap.md).

## Zahlen

Am 2026-09-10 auf dem Freigabestand in `main` selbst nachgemessen, nach dem
Zusammenführen mit dem v0.8.2-Hotfix:

| Prüfung | Ergebnis |
|---|---|
| Testsammlung | 5018 Tests in `tests/` (56 Browsertests separat, nicht in der CI) |
| SQLite, volle Suite | 5017 bestanden, 1 übersprungen (Exit 0) |
| MariaDB, volle Suite | 5017 bestanden, 1 übersprungen (Exit 0) |
| Testabdeckung | beide Datenbanken: 100 %, 8969 erfasste Anweisungen, keine ungedeckt; CI-Mindestschwelle 100 % |
| Ruff | ohne Befund (Exit 0) |
| mypy strict | ohne Befund, 124 Quelldateien (Exit 0) |
| Migrationskette | ein Kopf `d31f6a04c7e9`; Upgrade-/Downgrade-Tests gegen beide Datenbanken bestanden |
| Container | `docker build -f docker/Dockerfile .` erfolgreich (Exit 0) |

Je ein Test ist backendbedingt übersprungen: unter SQLite der MariaDB-spezifische
Parallelmigrationsfall, unter MariaDB die SQLite-spezifische Fremdschlüsselprüfung.

v0.9.0 ergänzt drei aufeinanderfolgende Migrationen nach `43aa18ba1c12`:
UI-Profil `c4d18b7e2a95`, Abwesenheit `c724de89a13f` und
Problemmeldung/`report.create` `d31f6a04c7e9`.

**Unabhängig nachvollzogen.** Ruff, mypy, beide Datenbanken, Alembic vorwärts und
rückwärts und der Container-Bau sind von einem Gegenleser, der nicht umgesetzt hat,
noch einmal selbst ausgeführt worden -- mit demselben Ergebnis, und mit Nachweis, dass
der MariaDB-Lauf wirklich gegen MariaDB lief (`11.8.9-MariaDB`) und nicht unbemerkt
auf SQLite auswich.

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`.
Der MariaDB-Lauf verwendet `mysql+pymysql` gegen `127.0.0.1:3306/doku_090` und
`COVERAGE_FILE=.coverage.doku`. Der Server meldet `11.8.9-MariaDB-ubu2404`;
während des Laufs waren drei Verbindungen zu `doku_090` und 42 Tabellen dort
nachweisbar. Die Migrationstests verwenden die abgeleitete Datenbank
`doku_090_migrations`. Der SQLite-Lauf verwendet `sqlite:///./test.db`.

## Was geschaltet wird — genau

- **`setting.control_armed`** ist der erste Riegel. Steht er auf `false`, geht an keinen
  Aktor etwas hinaus — das ist der Zustand einer neuen Anlage. `/control/arm` öffnet ihn,
  mit eigenem Recht `control.arm`.
- **Der MQTT-Client trägt einen zweiten, unabhängigen Riegel**, der beim Prozessstart
  gebaut wird. Scharfschalten wirkt deshalb erst nach einem Neustart.
- **Als dritter Riegel muss die Instanz führen** (`cluster.is_leader`); im
  Einzelbetrieb ohne Verbund-Datensatz gilt sie als führend. Sind alle drei
  Riegel offen, veröffentlicht der Dienst: Sollwerte an selbstregelnde
  Thermostatventile, Ein/Aus an gewöhnliche Zigbee2MQTT-Aktoren
  (`services/publishing.py::_send_actuator_switches`), Sollwert und `system_mode`
  (wo vorhanden) an Zigbee2MQTT-Thermostatventile ohne eigene Regelung
  (`Zigbee2MqttThermostat`), und Schaltbefehle an Meross-Steckdosen über eine
  zwischengespeicherte Cloud-Sitzung (`services/meross_session.py`), außerhalb jeder
  Datenbanktransaktion.
- **Ein gescheiterter Befehl wird jeden scharfen Zyklus erneut versucht** — unbegrenzt oft,
  bewusst ohne Backoff — und nur einmal pro Ausfallepisode geloggt; der
  Zwischenspeicher „nur bei Änderung senden" trägt das Ergebnis im Schlüssel, damit ein
  gescheiterter Befehl das Gerät nicht dauerhaft überspringt.
- **Das Schaltprotokoll** (`device_command`, `/device-commands`, Recht `audit.read`)
  zeichnet jeden Befehl auf, der hinausging oder im Trockenlauf unterdrückt oder
  verworfen wurde — Zeitpunkt, Zone, Gerät, Nutzlast, Ergebnis, Begründung, Auslöser.
  Ein Eintrag überlebt das Löschen oder Umbenennen seiner Zone oder seines Geräts
  (`SET NULL` plus Namens-Momentaufnahme) und unterliegt keiner automatischen
  Aufbewahrung — anders als Messwerte ist ein Schaltbefehl selten und jeder einzelne
  kann der Beleg sein, nach dem später jemand sucht. REST und MCP ziehen hier noch
  nicht nach (bewusste Entscheidung, keine übersehene Lücke).
- **`decide()` in `thermoctl/domain/control_loop.py`** berechnet `protection_allowed`
  einmal, oberhalb der Mindestschaltdauer-Regel; die Ausnahme von der Mindestschaltdauer
  gilt achsenabhängig — für einen gehaltenen Ein-Zustand reicht `valve_protection_active`
  allein, für den Aus-Timer zusätzlich `protection_allowed`. Ein Ventilschutzlauf, der
  seinen Vorrang mitten im Lauf verliert (Übersteuerung, „aus", Sensorausfall), hebt die
  Mindestschaltdauer nicht mehr auf — dieser Zusammenhang war einmal ein echter Fehler in
  der Regelkette (Taktschutz ausgehebelt für die Restdauer eines abgebrochenen
  Schutzlaufs) und ist die Eigenschaft, die `tests/test_control_loop_state_table.py`
  (2.376 erreichbare Kombinationen von 3.888 Rohkombinationen) mit erschöpft.

## Add-on-Betrieb

- **Optionsschema ist flach**: `secret_key`, `log_level`, `log_format`,
  `database_*` (fünf Felder), `mqtt_*` (neun Felder, inklusive `client_id` und
  `ca_cert`), `meross_email`/`meross_password`, `notify_webhook*`. Dazu ein freies Feld
  **`env`** (eine `NAME=WERT`-Zuweisung je Zeile) für jede `THERMOCTL_*`-Variable ohne
  eigenes Feld. Reihenfolge bei Überschneidung: dedizierte Felder, dann `env`, dann —
  gewinnt gegen beides — eine vom Betreiber tatsächlich gesetzte Umgebungsvariable.
  `docker/thermoctl_optionen.py` übersetzt; `ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN`
  dort sind gegen `Settings.model_fields` gewächtert
  (`test_every_settings_field_is_translated_or_deliberately_excluded`).
- **Das Abbild startet als root** und gibt die Rechte über `setpriv` an den
  unprivilegierten Benutzer `thermoctl` ab, bevor Migration und Dienst laufen — nötig,
  weil der Home-Assistant-Supervisor `/data/options.json` root:root anlegt. Der
  gewöhnliche `docker compose`-Betrieb mit explizit gesetztem `user:` bleibt unverändert
  unprivilegiert; ohne `user:`-Angabe läuft der Container kurz als root und fällt vor
  `alembic` zurück.
- **Ingress-Präfix**: wird pro Anfrage anhand der gegen `THERMOCTL_ROOT_PATH`
  geprüften Kopfzeile `X-Ingress-Path` gesetzt (Details oben). Lokale Verweise,
  Cookie-`path` und statische Dateien folgen dem jeweiligen Zugangsweg.
  `/healthz` bleibt direkt erreichbar.
- **Mehrarchitektur-Abbild**: `linux/amd64` und `linux/arm64`, `armv7` ausdrücklich
  nicht.
- **`tools/env_nach_addon.py`** übersetzt eine bestehende `.env` in die
  Add-on-YAML-Konfiguration — die Gegenrichtung von `docker/thermoctl_optionen.py`,
  aus denselben `ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN`-Konstanten abgeleitet statt
  doppelt gepflegt. `--ohne-datenbank` lässt alle `database_*`-Felder weg, für den Fall,
  dass die Datenbank im Add-on bereits eingetragen ist und nicht von einer
  `.env`-SQLite-Zeile aus der Entwicklungsumgebung überschrieben werden soll. Näheres in
  [self-hosting.md](self-hosting.md#6b-umstieg-von-docker-compose-auf-das-home-assistant-add-on).
- **Offen:** Ob und wie das Add-on Zugangsdaten des Home-Assistant-eigenen
  MQTT-Brokers automatisch übernimmt, ist nicht gebaut — `services: ["mqtt:want"]`
  gewährt nur Zugriff auf die Supervisor-API, füllt `options.json` nicht von selbst.

## Störungsmeldungen

Sechs Arten lassen sich anlagenweit einzeln abschalten, unter „Einstellungen“:
**Sensorstörung**, **Brücke oder Broker weg**, **Schaltbefehl gescheitert**,
**festhängender Messwert**, **Fenster-Alarm** und **Mieter-Problemmeldung**.
Die Schalter sind ab Werk an. Automatische Störungsmeldungen folgen den
Zustandsübergängen samt Entwarnung; Mieter-Problemmeldungen werden ausdrücklich
vom Mieter ausgelöst. Home Assistant bleibt entkoppelt: Wer den Webhook stilllegt,
verliert den Problemsensor dort nicht.

Ein **Testknopf** unter „Einstellungen" schickt eine gekennzeichnete Testmeldung über
denselben Weg wie eine echte und zeigt Statuscode, Dauer und im Fehlerfall den Grund
unmittelbar auf der Seite; ohne hinterlegten Webhook wird er nicht angeboten, gegen
wiederholtes Auslösen liegt ein Zeitabstand von zehn Sekunden davor. Daneben steht der
**Zustellzustand** — wann zuletzt versucht, mit welchem Ergebnis; „noch nie versucht"
ist ein eigener Zustand. Das Audit-Protokoll unterscheidet `sent` (Versuch ging los,
unabhängig vom Netzerfolg) von `suppressed` (durch einen abgeschalteten Schalter nie
versucht).

Die Grundfelder für Meldungsschalter und Zustellzustand stammen aus Migration
`67e794059830`; weitere Meldungsarten sind oben bei ihren Funktionen beschrieben.

## Kiosk, Ladeanzeige, Sprache

- **`kiosk.html`** trägt den AGPL-§13-Quelltextverweis knapp in der Kopfzeile
  (`target="_blank"`) statt einer Fußzeile — abweichend von der Fußzeile in `base_core.html`, weil
  das Wandtablett aus Distanz angesehen wird und die Fläche dem Zonenraster gehört.
- **Eine dezente Ladeanzeige** (`#tc-loading-bar`) läuft global auf jeder angemeldeten
  Seite und der Anmeldung/Einrichtung, gesteuert über die htmx-Ereignisse
  `htmx:beforeRequest`/`htmx:afterRequest`; erscheint erst nach 400 ms Verzögerung,
  respektiert `prefers-reduced-motion`. Bewusst nicht am Kiosk-Dashboard, dessen einzige
  htmx-Anfrage der selbsttätige 20-Sekunden-Nachlader ist.
- **Benutzersichtbarer Text schreibt echte Umlaute** (`ä`/`ö`/`ü`/`ß`), nicht mehr
  `ae`/`oe`/`ue`/`ss`. Bezeichner (Funktions-, Variablen-, Klassen-, Feld-,
  Spaltennamen), maschinell gelesene Schlüssel (YAML/JSON, Umgebungsvariablen,
  Migrationskennungen) und Dateinamen bleiben ASCII — das ist die Konvention für alles
  Neue. Der Wirkungswächter (`tests/test_user_visible_effect_texts.py`) prüft jede
  geänderte benutzersichtbare Zeile gegen `approved_physical_vocabulary.json` und fragt
  dafür `git`, nicht das Dateisystem — wichtig, falls je wieder eine Datei aus dem
  Repository entfernt wird, ohne aus der Versionsverfolgung zu verschwinden.

## Homebridge

`docs/homebridge.md` beschreibt die Einbindung über den `mqtt-thing`-Zusatz gegen
dieselben MQTT-Topics wie Home Assistant — keine eigene thermoctl-Integration, reine
Dokumentation mit Wächtertests gegen den Topic-Vertrag.

## Was nur der Projektinhaber entscheiden kann

- **Phase 2 wirklich abschließen.** Ein Auszug der Produktivdatenbank wurde am
  2026-09-04 gegen die drei Abnahmekriterien geprüft
  ([phase-2-abnahme.md](phase-2-abnahme.md)) — **nicht abnahmereif**: Fünf der sechs
  Zonen wurden erst nach dem Scharfschalten angelegt und liefen nie im
  Schattenbetrieb; Kriterium 3 (Altsystemvergleich) entfällt ersatzlos, weil der
  Vergleichsbetrieb übersprungen wurde. Eine Zone braucht mehrere Tage Schattenbetrieb
  ab ihrer Anlage, bevor das Kriterium für sie erfüllbar ist.
- **Die Ablösung des Altsystems** (Host, vier Skripte) ist noch nicht vollzogen; es
  läuft weiter als Rückfallebene.
- **Automatische Übernahme der Home-Assistant-eigenen MQTT-Broker-Zugangsdaten** ins
  Add-on ist nicht gebaut (siehe oben).

Der Sicherheitsstand steht in
[sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md), am
2026-09-04 gegen den aktuellen Code nachgeprüft (siehe deren einleitender Nachtrag):
Von acht Hoch-/Mittel-Befunden sind sechs behoben oder teilweise behoben
(`device.manage` zonenübergreifend, Kiosk-Token als Bearer-Ersatz, Login-Blockade des
Regelzyklus, Meross-Bestätigung, Webhook-Weiterleitung, Passwortwechsel/Sitzungswiderruf;
das Einrichtungs-Token zusätzlich befristet). **Weiterhin offen:** der MQTT-Befehlspfad
(dokumentiert, im Code nicht erzwingbar — Frage der Broker-Konfiguration), unbegrenzte/
unmaskierte Meross-Cloud-Antworten, das unbegrenzt wachsende Schaltprotokoll, die
HTTP-Netzwerkvorgabe (`0.0.0.0` ohne TLS-Erzwingung), und die CSRF-Ausnahme für
Login/Logout.
