# Sicherheitsdurchsicht

Stand: 2026-08-29. Vor der Veröffentlichung erneut durchzugehen (Phase 5, Aufgabe 8) —
diese Fassung deckt den Stand nach Teilprojekt 2 und dem größeren Teil von Teilprojekt 3 ab.

Der Dienst steuert eine Heizung in einer bewohnten Wohnung und soll öffentlich werden.
Beides zusammen macht diese Durchsicht zu mehr als einer Formalie.

## 1. Was geprüft wurde und was dabei herauskam

| Prüfung | Ergebnis |
|---|---|
| Zugangsdaten im Repo (Muster für Passwörter, Schlüssel, Tokens) | keine |
| `.env` versehentlich eingecheckt | nein, nur `.env.example` ohne Werte |
| Fremde Adressen im Quelltext | eine (Meross-Cloud), inzwischen konfigurierbar |
| Roh-SQL oder zusammengebaute Abfragen | keine; alle Filter über SQLAlchemy-Ausdrücke |
| Ändernde Routen ohne CSRF-Schutz | keine — `tests/test_csrf.py` hält das nach |
| Ansichten ohne Rechteprüfung | keine; siehe Abschnitt 2 |
| Geheimnisse im Log | maskiert; eine bewusste, dokumentierte Ausnahme |
| Ausgehende Verbindungen | nur MQTT und Meross, beide nur bei hinterlegter Konfiguration |

## 2. Rechteprüfung je Adapter

Alle drei Adapter benutzen dieselben Funktionen — `require()`, `hat_recht()` und
`visible_zones()` aus `thermoctl/domain/authz.py`. Es gibt keine zweite Umsetzung, und
damit keinen Weg, der mehr darf als ein anderer.

Nicht jede Route ruft `require()` selbst auf; einige gehen über `_sichtbare_zone()`, das
`visible_zones()` mit dem passenden Recht filtert und sonst `404` liefert. Das ist
Absicht: **Eine fremde Zone ist nicht auffindbar, nicht verboten** — ein `403` verriete,
dass es sie gibt.

Ohne Prüfung sind ausschließlich:

- `GET /login`, `POST /login`, `POST /logout` — vor der Anmeldung.
- `GET /setup`, `POST /setup` — durch das Einmal-Token abgesichert und dauerhaft
  geschlossen, sobald ein Benutzer existiert.
- `GET /` — filtert selbst über `visible_zones()` und leitet ohne Sitzung zur Anmeldung.
- `GET /healthz` — verrät nur Zustand und Version.

## 3. Das Verhalten, auf das es ankommt

**Man kann sich nicht aussperren.** Der letzte aktive Benutzer mit `user.manage` lässt sich
nicht deaktivieren, und die letzte Gruppe, über die dieses Recht läuft, lässt sich weder
löschen noch entrechten. Beide Wege sind gesperrt und einzeln getestet. Ohne diese Sperre
genügt ein Fehlgriff, um eine laufende Heizungssteuerung unbedienbar zu machen.

**Ein nicht zonenbezogenes Recht kann keine Zone tragen.** Sonst stünde `user.manage` mit
Zoneneinschränkung in der Rechteliste und griffe nie — eine Rechtevergabe, die aussieht,
als hätte sie gewirkt.

**Ein Token kann höchstens, was sein Besitzer kann** — geprüft zur Laufzeit, nicht nur beim
Ausstellen. Verliert der Besitzer ein Recht, verliert es das Token ebenfalls.

**Die Anmeldung verrät nicht, welche Konten es gibt.** Gleiche Meldung, gleiche Rechenzeit:
Die Passwortprüfung läuft auch bei unbekanntem Benutzernamen, gegen einen Wegwerf-Hash.
Ohne das wäre Argon2id selbst der Seitenkanal.

**Nichts wird geschaltet.** Zwei unabhängige Riegel: `setting.control_armed` und ein
Client, der nur scharf gebaut veröffentlicht. Tests belegen beide Richtungen.

## 4. Kryptografie

- Passwörter: Argon2id (`argon2-cffi`, Standardparameter), Mindestlänge 12 Zeichen.
- API-Tokens und Einrichtungs-Token: 256 Bit Zufall aus `secrets`, gespeichert als
  SHA-256. Ein langsamer Hash trägt bei 256 Bit Zufall nichts bei, kostet aber bei jeder
  Anfrage Rechenzeit — bei Passwörtern gilt das Gegenteil.
- CSRF-Token: HMAC-SHA256 über das Sitzungsgeheimnis, verglichen mit
  `hmac.compare_digest`.
- Sitzungscookie: `httponly`, `samesite=lax`, `secure` abhängig von
  `THERMOCTL_SECURE_COOKIES`.

## 5. Was offen bleibt

- **`THERMOCTL_SECURE_COOKIES` steht standardmäßig auf `false`**, weil die Erstinbetrieb-
  nahme sonst über `http://` scheitert. Wer den Dienst ins Netz stellt, muss es setzen —
  das steht in [self-hosting.md](self-hosting.md), aber der Dienst erzwingt es nicht.
  **Erledigt:** Der Dienst warnt jetzt beim Start, wenn er auf eine nicht-lokale Adresse
  gebunden ist und `secure_cookies` aus steht. Erzwingen kann er es nicht — hinter einem
  Reverse-Proxy sieht er nur HTTP —, aber sagen kann er es.
- **Keine Anmeldebegrenzung über Prozessgrenzen hinweg.** Die Drosselung zählt
  Fehlversuche im Prozessspeicher; ein Neustart setzt sie zurück. Für eine
  Heimnetzinstallation vertretbar, für einen öffentlich erreichbaren Dienst nicht — und
  öffentlich erreichbar soll er ohnehin nicht sein.
- **Keine Kontosperre**, ausdrücklich: In einem Einhaushalt-System wäre sie vor allem eine
  bequeme Möglichkeit, sich selbst auszusperren.
- **Der Meross-Nutzlastaufbau ist ungeprüft** — siehe
  [offene-entscheidungen.md](offene-entscheidungen.md). Kein Sicherheitsmangel, aber ein
  Punkt, an dem Zugangsdaten an einen fremden Dienst gehen, sobald er scharf ist.
- **Das Einrichtungs-Token steht im Log.** Bewusst, und der einzige Kanal dorthin. Wer
  Logs weiterleitet, leitet es mit weiter. In der Self-Hosting-Anleitung benannt.

## 6. Vor dem Öffentlichschalten zu tun

1. Diese Durchsicht auf den dann aktuellen Stand ziehen.
2. Die Git-Historie auf versehentlich eingecheckte Zugangsdaten prüfen — nicht nur den
   aktuellen Baum. Das Repo war bisher privat; was einmal in einem Commit steht, bleibt
   dort.
3. Entscheiden, ob die Startwarnung aus Abschnitt 5 gebaut wird.
4. Die Abhängigkeiten auf bekannte Schwachstellen prüfen.
