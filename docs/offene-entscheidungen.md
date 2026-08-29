# Selbst getroffene Entscheidungen

Entscheidungen, die sonst eine Rückfrage an den Projektinhaber gewesen wären. Der Auftrag
für die autonome Arbeit lautet: die naheliegendste treffen, hier mit Begründung und
verworfenen Alternativen festhalten, weiterarbeiten. Wer eine davon anders will, ändert sie
— sie sind dokumentiert, nicht in Stein.

---

## 2026-08-29 — CSRF-Schutz hängt am Router, nicht an der Route

**Entschieden:** `csrf_schutz` ist eine FastAPI-Abhängigkeit, die über
`APIRouter(dependencies=[Depends(csrf_schutz)])` an *jedem* Router der Oberfläche hängt.
Sie greift nur bei zustandsändernden Methoden und nur, wenn die Anfrage ein Sitzungscookie
trägt. Dazu kommt ein Wächtertest (`tests/test_csrf.py`), der jede zustandsändernde Route
aufzählt und rot wird, sobald eine ohne diesen Schutz dazukommt.

**Warum:** Die Auflage aus dem Abschlussreview war nicht „weniger Codewiederholung", sondern
„irgendwann vergisst man sie". Eine gemeinsame Funktion, die man je Route aufrufen muss,
löst das nicht — man vergisst dann den Aufruf. Erst die Kombination aus Router-Abhängigkeit
(neue Routen sind von sich aus geschützt) und Wächtertest (ein neuer Router ohne den Schutz
fällt auf) beseitigt das Vergessen als Fehlerquelle.

**Verworfen:**
- *Middleware für alle unsicheren Methoden.* Wirkt global, ist aber nicht mehr
  routenweise ausnehmbar, ohne Pfade als Zeichenketten zu vergleichen. Die REST-API
  braucht eine Ausnahme, und eine Pfadliste in einer Middleware verfällt still.
- *Prüfung je Route wie bisher, nur in eine Hilfsfunktion ausgelagert.* Genau das
  Vergessen, das die Auflage beseitigen wollte.

**Ausnahme, bewusst:** Die REST-API (`/api/…`) hängt nicht am CSRF-Schutz. Sie wertet
ausschließlich den `Authorization`-Header aus und niemals ein Cookie — ohne
Cookie-Authentifizierung gibt es keinen CSRF-Weg. Damit diese Ausnahme nicht still
ungültig wird, hält `test_api_nimmt_kein_sitzungscookie_an` genau das nach.

---

## 2026-08-29 — Zwei Wächtertests liefen ins Leere (Fund, keine Wahl)

Kein Ermessen, aber festhaltenswert, weil es die dritte Fehlerklasse derselben Art ist.

Seit FastAPI 0.141 legt `include_router()` keine flache Routenliste mehr an: In
`app.routes` steht ein `_IncludedRouter`, der den ursprünglichen Router unter
`original_router` trägt. Beide Wächter — `test_endpunktabdeckung.py` und der neue
`test_csrf.py` — filterten `app.routes` auf `APIRoute` und fanden dadurch nur noch
`/healthz`. Sie waren grün, weil sie nichts mehr prüften.

Behoben durch `tests/hilfen.alle_api_routen()`, das über `original_router` absteigt, und
durch eine Gegenprobe für beide Wächter (Schutz entfernen beziehungsweise ungetestete Route
ergänzen — beide werden rot).

Dabei fiel ein zweiter Mangel auf: Die Endpunktabdeckung wertete ihre Mitschrift **mitten
im Lauf** aus, nach Dateinamen sortiert vor `test_rauchtest.py`. Sie hätte alles danach
Aufgerufene als ungeprüft gemeldet. `pytest_collection_modifyitems` zieht sie jetzt ans
Ende des Laufs.

**Lehre, die zur bestehenden passt:** Ein Wächtertest braucht seine Gegenprobe nicht nur
beim Schreiben, sondern nach jedem Versionssprung der Bibliothek, deren Interna er abfragt.

---

## 2026-08-29 — Testdaten sind anonymisiert, nicht die Originaldatei

**Entschieden:** `tests/daten/anlage-beispiele.json` ist eine anonymisierte Fassung von
`.superpowers/sdd/anlage-beispiele.json`. Struktur, Feldnamen und alle Werteigenheiten sind
unverändert; nur die Gerätenamen sind ersetzt. Die Originaldatei bleibt außerhalb des Repos
(`.superpowers/sdd/` ist vollständig gitignored).

**Warum:** Die echten Namen enthalten die Vornamen der Bewohner und die Zimmeraufteilung
einer bestimmten Wohnung. Das Repo soll veröffentlichbar sein. Grundsatz 2 nennt Secrets,
meint aber dasselbe Prinzip: Was nicht ins Repo gehört, gehört auch nicht als Testdatum
hinein.

Erhalten bleiben genau die Eigenheiten, an denen die Auswertung scheitern könnte:
Leerzeichen und Umlaute in Namen (`Über Küche`), Gruppen in Kleinschreibung, der Eintrag
`bridge`, `null`-Werte, verschachtelte Objekte, `voltage` einmal in Millivolt und einmal in
Volt.

**Verworfen:**
- *Originaldatei mit committen.* Löst das Problem der Aussagekraft, schafft ein größeres.
- *Testdaten frei erfinden.* Genau das, was der Auftrag ausschließt — „bau das
  Nutzlastformat dagegen, nicht gegen Vermutungen". Eine erfundene Nachricht hätte weder
  `philips_raw` noch `voltage: 230` an einer Netzsteckdose.
- *Tests nur örtlich gegen die echte Datei laufen lassen.* Dann prüft die CI das
  Nutzlastformat nie.

---

## 2026-08-29 — Die alten MQTT-Topics werden in Phase 2 weder bedient noch gelesen

**Entschieden:** `thermoctl` veröffentlicht in Phase 2 nichts und abonniert `heizung/#`
nicht. Die Roadmap stellt die Frage, ob die Alt-Topics übergangsweise mitbedient werden;
die Antwort für diese Phase ist nein.

**Warum:** Bedienen hieße veröffentlichen, und veröffentlichen ist genau das, was der
Trockenlauf ausschließt — Home Assistant und das Altsystem hören auf diesen Topics mit.
Zwei Schreiber auf demselben Topic sind zudem der zuverlässigste Weg, einen Fehler zu
erzeugen, den niemand mehr zuordnen kann, solange das Altsystem die Rückfallebene ist.

Das *Lesen* von `heizung/#` wäre gefahrlos und liefert die Vergleichsdaten für Phase 4.
Es gehört trotzdem dorthin und nicht hierher: Der Vergleichsbetrieb ist eine
Phase-4-Aufgabe mit eigenem Datenmodell (Abweichungsbericht), und ein halb gebauter
Vergleich, der Daten sammelt, die niemand auswertet, ist Ballast.

**Verworfen:**
- *Alt-Topics zusätzlich bedienen.* Verstößt gegen den Trockenlauf.
- *`heizung/#` schon jetzt mitschreiben.* Sinnvoll, aber Phase 4 — dort mit dem
  Datenmodell, das der Abweichungsbericht wirklich braucht.

---

## 2026-08-29 — Der Meross-Adapter bringt keine neue Abhängigkeit mit

**Entschieden:** Der Meross-Adapter spricht die Cloud-HTTP-Schnittstelle selbst an, statt
`meross_iot` einzubinden. Ohne hinterlegte Zugangsdaten meldet er sich als „nicht
konfiguriert" und tut nichts.

**Warum:** Die Meross-Cloud ist laut Roadmap eine Fremdabhängigkeit ohne Zusicherung. Eine
Bibliothek dafür ins Projekt zu ziehen, das eine Heizung steuert, vergrößert die
Angriffsfläche und die Abhängigkeitskette für einen Adapter, der in dieser Phase ohnehin
nichts schaltet. Was wir wirklich brauchen — Geräteliste und Schaltbefehl — sind zwei
HTTP-Aufrufe.

**Verworfen:**
- *`meross_iot` einbinden.* Bequemer, aber eine große Abhängigkeit für zwei Aufrufe, und
  sie bringt einen eigenen Anmelde- und Ereignisapparat mit.
- *Meross vorerst weglassen.* Die Anlage schaltet über Meross-Steckdosen; ohne den Adapter
  fehlt der Hälfte der Aktoren die Anbindung, und Phase 4 stünde ohne sie da.

**Offen für den Projektinhaber:** Die Zugangsdaten (E-Mail und Passwort des Meross-Kontos)
gehören in `.env`. Ohne sie bleibt der Adapter unkonfiguriert — das ist in Phase 2 kein
Mangel, in Phase 4 aber ein Blocker.

---

## 2026-08-29 — Die Regelentscheidung wird in Phase 2 gebaut, nicht erst in Phase 4

**Entschieden:** `thermoctl/domain/regelung.py` — Hysterese, Mindestschaltdauer,
Fensterpause, Frostschutz bei Sensorausfall — entsteht in Phase 2 als reine Funktion und
wird erschöpfend getestet. Geschaltet wird damit nichts.

**Warum:** Das Schattenprotokoll ist der erklärte Zweck dieser Phase, und es hat ohne eine
Entscheidung nichts zu protokollieren. Die Funktion ist rein: kein Netz, keine Uhr, keine
Datenbank — sie kann nichts anfassen. Phase 4 verdrahtet sie nur noch mit den Aktoren.
Der Auftrag deckt das ausdrücklich: „Bau die Logik und die Tests, aber schalte nichts
scharf."

**Verworfen:** *Ein vereinfachter Schattenentscheider nur für Phase 2.* Dann vergliche
Phase 4 das Altsystem gegen eine Logik, die anschließend durch eine andere ersetzt wird —
der Vergleich wäre wertlos.

---

## 2026-08-29 — Bei ausgefallenem Sensor wird auf Frostschutz geregelt, nicht abgeschaltet

**Entschieden:** Meldet die Störungserkennung `veraltet` — es liegt ein letzter bekannter
Messwert vor, der aber zu alt ist —, ersetzt die Regelung den aufgelösten Sollwert durch
den **Frostschutz-Sollwert** und regelt damit normal weiter. Nur wenn gar kein Wert
vorliegt (`keine_quelle`), bleibt das Ventil zu.

**Warum:** Die erste Umsetzung schaltete bei jedem Sensorausfall dauerhaft ab. Das klingt
vorsichtig, ist aber die gefährlichere Antwort: Eine leere Batterie im Januar heißt dann,
dass ein Raum unbegrenzt auskühlt — und genau daran friert eine Leitung ein. Der
Rahmenentwurf nennt dieselbe Sache an anderer Stelle als Defekt des Altsystems („Aus heißt
Frostschutz, nicht stromlos").

Die Gegenrichtung, auf den regulären Sollwert weiterzuheizen, ist ebenso falsch: Der Wert,
gegen den geprüft würde, ist ja gerade der unzuverlässige. Der Frostschutzwert ist der
Kompromiss, für den er da ist — tief genug, dass ein falscher Messwert höchstens auf ein
unbedenkliches Niveau führt, hoch genug gegen Frost.

**Restrisiko, benannt:** Bleibt ein Sensor dauerhaft bei einem zu kalten Wert stehen, hält
die Anlage die Zone auf Frostschutzniveau, statt abzuschalten. Das kostet Energie und ist
unangenehm, richtet aber keinen Schaden an. Eine Obergrenze für die ununterbrochene
Heizdauer wäre die vollständige Antwort; sie gehört zu Phase 4, wo wirklich geschaltet wird.

**Verworfen:**
- *Bei jedem Sensorausfall abschalten.* Die ursprüngliche Umsetzung; siehe oben.
- *Auf dem letzten Sollwert weiterregeln.* Verlässt sich auf genau die Messung, der man
  gerade nicht mehr traut.

**Für den Projektinhaber:** Diese Entscheidung gehört zu den wenigen, die vor dem
Scharfschalten in Phase 4 ausdrücklich bestätigt werden sollten. Sie steht deshalb hier
und nicht nur im Quelltext.

---

## 2026-08-29 — Der Meross-Nutzlastaufbau ist eine begründete Annahme, kein geprüfter Code

**Festgehalten, nicht entschieden.** Der Meross-Adapter ist vollständig verdrahtet: Er
prüft `control_armed`, bildet Anmeldung und Schaltaufruf, behandelt Fehler und ist im
Trockenlauf getestet. Was er **nicht** ist: gegen ein echtes Meross-Konto ausgeführt.

Der Grund ist die Phase selbst — es liegen keine Zugangsdaten vor, und der Trockenlauf
verbietet den Versuch. Meross verlangt je nach Firmwarestand eine signierte Nutzlast
(Zeitstempel, Nonce, Prüfsumme); ob der hier gebaute Aufruf so akzeptiert wird, ist offen.

Das steht so im Docstring des Adapters und hier, statt dass der Code fertig aussieht und
beim ersten scharfen Schalten scheitert. **Vor dem Scharfschalten in Phase 4 gehört genau
dieser Aufruf einmal gegen die echte Cloud geprüft.**

**Was der Projektinhaber dafür braucht:** die Zugangsdaten des Meross-Kontos in `.env`
(`THERMOCTL_MEROSS_EMAIL`, `THERMOCTL_MEROSS_PASSWORD`) und, falls die Geräte außerhalb
Europas angemeldet sind, `THERMOCTL_MEROSS_API_BASE`.
