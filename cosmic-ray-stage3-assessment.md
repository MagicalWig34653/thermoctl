# Mutationstest Runde 1, Stufe 3

Werkzeug: Cosmic Ray 8.7.0. Die acht TOML-Dateien daneben enthalten je genau
eine Zieldatei, die zugehörigen direkten und sicherheitsrelevanten
Integrationstests, Zeitlimit und lokalen Verteiler. Die Sitzungsdatenbanken und
vollständigen Worker-Logs liegen nur unter `/tmp` und gehören weder ins
Repository noch in die CI.

`auth/__init__.py` ist leer und erzeugt keine Mutanten. Ein `auth/passkeys.py`
existiert in dieser Fassung nicht. `auth/secrets.py` wurde zusätzlich zu den im
Auftrag besonders genannten Dateien einbezogen, weil es innerhalb des
Verzeichnisumfangs die Sitzung- und Token-Secrets erzeugt.

Wie in Stufe 1 und 2 wurde der dort dokumentierte Operatorfilter unverändert
übernommen. Er legt Kreuztyp-Ersetzungen von Union- und SQLAlchemy-Operatoren
sowie nicht benachbarte Vergleichsoperatoren ab. Die Zahlen beziehen sich wie
in den vorherigen Bewertungen trotzdem auf alle von Cosmic Ray erzeugten Jobs;
gefilterte Jobs stehen als `SKIPPED` in der Sitzungsdatenbank.

## Gefilterter Ausgangslauf

| Datei | Mutanten | überlebt | Anteil |
|---|---:|---:|---:|
| `domain/authz.py` | 106 | 4 | 3,77 % |
| `auth/csrf.py` | 14 | 0 | 0,00 % |
| `auth/dependencies.py` | 28 | 11 | 39,29 % |
| `auth/kiosk.py` | 25 | 0 | 0,00 % |
| `auth/passwords.py` | 14 | 3 | 21,43 % |
| `auth/sessions.py` | 108 | 29 | 26,85 % |
| `auth/tokens.py` | 92 | 3 | 3,26 % |
| `auth/secrets.py` | 17 | 2 | 11,76 % |
| **gesamt** | **404** | **52** | **12,87 %** |

Jeder der 52 Ausgangsüberlebenden ist im Folgenden einem Test oder einer
begründeten Ablage zugeordnet. Mehrere Mutanten derselben Aussage sind
zusammengefasst; die Zahl in Klammern zählt weiterhin jeden einzelnen.

## Durch Tests erschlagen — 49

### `domain/authz.py` — 4

- Ablaufvergleich `<=` zu `<` (1):
  `test_a_token_expires_at_the_exact_boundary`. Im Ernstfall hätte ein Token
  exakt an seinem Ablaufzeitpunkt noch die Rechte seines Besitzers getragen.
- Zonenangabe in `Forbidden` bei `is not None` vertauscht beziehungsweise
  negiert (2): `test_denial_message_names_even_a_zero_zone_id`. Das verschiebt
  keine Erlaubnisgrenze, würde aber bei einer verweigerten Aktion die Zone
  verschweigen und damit den Sicherheitsbefund irreführend erklären.
- Filter für sichtbare Zonen von `code and zone_id` zu `code or zone_id` (1):
  `test_visible_zones_ignores_grants_for_other_permission_codes`. Im Ernstfall
  hätte irgendein zonenbezogenes Recht — etwa `zone.manage` — die Zone auch in
  einer Liste für ein anderes Recht wie `zone.read` sichtbar gemacht. Das ist
  eine echte Rechteausweitung und musste sterben.

Die bereits vorhandenen Tests erschlugen weiterhin beide Richtungen der
entscheidenden Bereichsregel: Ein anlagenweites Recht deckt jede Zone ab; ein
zonenbezogenes Recht deckt weder andere Zonen noch die Anlage als Ganzes ab.
Damit bleibt auch die von `domain/administration.py` benutzte Rechteprüfung
gegen das Aussperren des letzten Verwalters an dieser Grenze abgesichert, ohne
die fremde Produktionsdatei zu ändern.

### `auth/dependencies.py` — 10

- Fehlendes, unbekanntes oder zu einem fehlenden/inaktiven Benutzer gehörendes
  Sitzungscookie durch Vertauschen/Negieren der vier Bedingungen (8): die
  bestehenden Tests `test_a_protected_page_without_a_cookie_is_401`,
  `test_a_protected_page_with_an_unknown_cookie_is_401` und
  `test_a_protected_page_for_an_inactive_user_is_401` wurden in den
  reproduzierbaren Testbefehl aufgenommen. Im Ernstfall ließ insbesondere die
  Mutation `user is None or not user.is_active` zu `and` einen deaktivierten
  Benutzer mit seiner laufenden Sitzung weiter hinein; andere Varianten
  sperrten gültige Benutzer aus oder brachen statt der einheitlichen 401 ab.
- Formularwert nur bei einem Nicht-String übernehmen (1):
  `test_a_valid_token_in_a_form_field_goes_through_without_a_header`. Damit
  sterben Mutanten, die den Formularweg entfernen; die vorhandenen Header- und
  Kiosk-Browsertests sichern unabhängig den Headerweg. Ohne den neuen Test wären
  normale HTML-Formulare trotz gültigem CSRF-Token als veraltet abgewiesen
  worden.
- Rollback nur noch für eine Cosmic-Ray-Ausnahme (1): der bereits vorhandene
  `test_get_session_rolls_back_on_error`, nun Teil des Testbefehls. Im Ernstfall
  wäre nach einem Anwendungsfehler kein ausdrücklicher Rollback erfolgt.

Die vorhandenen Recovery-Tests prüfen beide Pfade `/login` und `/logout`: Bei
veraltetem Token werden Cookies gelöscht und nach `/login?stale=1`
weitergeleitet. `test_a_logout_without_a_csrf_token_is_not_carried_out` und
`test_a_logout_with_a_token_from_a_foreign_session_revokes_nothing` beweisen
zusätzlich, dass die angeforderte Aktion dabei nicht ausgeführt wird.

### `auth/passwords.py` — 3

- Mindestgrenze `<` zu `<=` und Konstante 12 zu 11 beziehungsweise 13 (3):
  `test_the_documented_minimum_password_length_is_accepted`. Eine Verschiebung
  nach unten hätte zu kurze Passwörter zugelassen; eine Verschiebung nach oben
  hätte ein laut Meldung gültiges Passwort ausgesperrt.

### `auth/sessions.py` — 29

- Operator- und Zahlenersetzungen im 14-Tage-Fallback (26):
  `test_the_missing_settings_fallback_is_exactly_fourteen_days`. Im Ernstfall
  hätte dies Sitzungen ohne vorhandene Settings-Zeile von Sekunden bis zu sehr
  langen Zeiträumen gültig gemacht oder legitime Sitzungen unerwartet früh
  beendet.
- Ablaufvergleich `<=` zu `<` (1):
  `test_a_session_expires_at_the_exact_boundary`. Exakt abgelaufene Sitzungen
  dürfen keinen weiteren Request authentifizieren.
- Zwei `or`-Verknüpfungen zu `and` (2):
  `test_unknown_and_revoked_sessions_are_both_rejected`. Im Ernstfall konnte
  ein widerrufenes, noch nicht abgelaufenes Sitzungscookie wieder als gültig
  aufgelöst werden; ein unbekanntes Cookie führte je nach Auswertung zum
  Abbruch statt zur Ablehnung.

### `auth/tokens.py` — 2

- Ablaufvergleich `<=` zu `<` (1):
  `test_a_token_expires_at_the_exact_boundary`. Im Ernstfall war ein Token am
  exakten Ablaufzeitpunkt noch verwendbar.
- Formatprüfung `len != 3 or prefix != tctl` zu `and` (1): die Ergänzung
  `resolve_token(session, "tctl")` in
  `test_resolving_a_token_with_invalid_format_returns_none`. Im Ernstfall
  führte ein entsprechend verstümmelter Bearer-Token zu einem Serverfehler
  statt zur sicheren Ablehnung.

### `auth/secrets.py` — 1

- Zufallsinput von 32 auf 33 Bytes (1):
  `test_a_secret_contains_exactly_256_bits_of_random_input`. Das wäre keine
  Schwächung, widerspräche aber der ausdrücklichen 256-Bit-Zusicherung und wird
  deshalb nicht stillschweigend als beliebig behandelt.

## Begründet abgelegt — 3

- `auth/dependencies.py`, `StalePage.__init__`, keyword-only zu positional-only
  (1): Alle Aufrufe verwenden weiterhin `recovery=...`; Parametername, Wert,
  Exception und Laufentscheidung bleiben identisch. Die Mutation verändert nur,
  welche zusätzliche, im Projekt nicht verwendete Aufrufschreibweise erlaubt
  wäre.
- `auth/tokens.py`, `issue_token`, keyword-only zu positional-only (1): Alle
  Aufrufer übergeben die bisherigen Positionsparameter weiterhin positionell
  und `is_kiosk` weiterhin als Keyword. Tokeninhalt, Rechteprüfung und
  Laufentscheidung bleiben identisch.
- `auth/secrets.py`, `PREFIX_LAENGE` 8 zu 9 (1): Der Wert fließt ausschließlich
  als `secrets.token_hex(PREFIX_LAENGE // 2)` ein. Sowohl `8 // 2` als auch
  `9 // 2` sind 4; beide Fassungen erzeugen exakt dieselben acht Hexzeichen aus
  vier Zufallsbytes. Der Mutant ist daher mathematisch äquivalent.

## Endstand

| Datei | Mutanten | überlebt | Anteil | Bewertung |
|---|---:|---:|---:|---|
| `domain/authz.py` | 106 | 0 | 0,00 % | keine offenen Mutanten |
| `auth/csrf.py` | 14 | 0 | 0,00 % | keine offenen Mutanten |
| `auth/dependencies.py` | 28 | 1 | 3,57 % | gleichwertig |
| `auth/kiosk.py` | 25 | 0 | 0,00 % | keine offenen Mutanten |
| `auth/passwords.py` | 14 | 0 | 0,00 % | keine offenen Mutanten |
| `auth/sessions.py` | 108 | 0 | 0,00 % | keine offenen Mutanten |
| `auth/tokens.py` | 92 | 1 | 1,09 % | gleichwertig |
| `auth/secrets.py` | 17 | 1 | 5,88 % | gleichwertig |
| **gesamt** | **404** | **3** | **0,74 %** | **alle drei gleichwertig** |

Nicht abgelegte, fachlich relevante Überlebende: **0**. Es wurde kein Fehler in
der Produktionslogik gefunden und keine Produktionsdatei geändert; sämtliche
fachlichen Befunde waren fehlende oder im Cosmic-Ray-Befehl zuvor nicht
einbezogene Testaussagen.
