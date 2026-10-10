# Glossar

<!-- Diese Datei wird erzeugt. Nicht von Hand ändern: Quelle ist thermoctl/data/glossar.json, Erzeugung mit `python -m tools.glossar_erzeugen`. -->

Die Fachbegriffe der Oberfläche in einfachen Worten. Dieselben Erklärungen stehen in der Weboberfläche unter `/glossar`, und die kleinen Fragezeichen neben Einstellungen führen direkt hierher.

**Bereiche:** Regelung · Sensorik · Betrieb · Oberfläche — der Bereich steht unter jedem Begriff.

[A](#a) · [B](#b) · [E](#e) · [F](#f) · [G](#g) · [H](#h) · [K](#k) · [M](#m) · [N](#n) · [P](#p) · [R](#r) · [S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w) · [Z](#z)

## A

<a id="abwesenheit"></a>
### Abwesenheit

*Regelung* — Eine Übersteuerung für die eigenen Räume eines Bewohners, gültig bis zu einem Rückkehrdatum.

Die Abwesenheit wird in der Wohnungssicht gesetzt. Sie ist eine Übersteuerung mit gemeinsamem Ende; der Wochenplan selbst wird dadurch nicht verändert. Nicht zu verwechseln mit dem anlagenweiten Urlaub, den die Verwaltung setzt.

Auch: Zurück am

Siehe auch: [Urlaub](#urlaub), [Übersteuerung](#uebersteuerung), [Anlagensicht und Wohnungssicht](#oberflaechen)

<a id="aktiv-bereitschafts-verbund"></a>
### Aktiv-Bereitschafts-Verbund

*Betrieb* — Mehrere Instanzen teilen sich eine Datenbank; eine ist führend, die anderen stehen bereit.

Fällt die führende Instanz aus, kann eine Bereitschaftsinstanz übernehmen; die Zahl der abgewarteten Regelzyklen ist einstellbar (ab Werk 5). Was eine Instanz in Bereitschaft tut und was nicht, steht in docs/self-hosting.md im Abschnitt zum Aktiv-Bereitschafts-Verbund.

Auch: Verbund, Bereitschaft, Führend

Siehe auch: [Regelzyklus](#regelzyklus), [Scharfschalten](#scharfschalten)

<a id="aktor"></a>
### Aktor

*Betrieb* — Ein Gerät, das Befehle der Regelung entgegennimmt.

Beispiele sind Schaltsteckdosen und Relais (Ein/Aus) sowie Thermostate mit Zieltemperatur. Zu welcher Zone ein Gerät gehört und ob es sich selbst regelt, wird bei den Geräten der Zone festgelegt. Ob Befehle tatsächlich gesendet werden, hängt vom Trockenlauf und vom Scharfschalten ab.

Auch: Stellglied, Schalter

Siehe auch: [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil), [Trockenlauf](#trockenlauf), [Zone](#zone)

<a id="oberflaechen"></a>
### Anlagensicht und Wohnungssicht

*Oberfläche* — Zwei Oberflächen: eine für Verwaltung und Technik, eine für Bewohner.

Die Gruppe eines Benutzers legt fest, welche Oberfläche er sieht. Die Anlagensicht zeigt die ganze Anlage mit Geräten, Regelparametern und Protokollen. Die Wohnungssicht ist für Bewohner gedacht und zeigt die eigenen Räume mit Temperatur, Wochenplan, Heizzeit und Abwesenheit, möglichst ohne Fachbegriffe.

Auch: Anlagensicht, Wohnungssicht, Mietersicht, Profil

Siehe auch: [Gruppen und Rechte](#gruppen-und-rechte), [Abwesenheit](#abwesenheit), [Kiosk](#kiosk)

<a id="api-token"></a>
### API-Token

*Oberfläche* — Ein Zugangsschlüssel für Programme, die thermoctl über die Schnittstellen bedienen.

Ein Token trägt eigene Rechte, höchstens die seines Besitzers (siehe docs/api.md). Der Klartext wird bei der Ausstellung angezeigt; gespeichert wird nur ein Hash. Wer ihn verliert, stellt ein neues Token aus.

Auch: Token, Zugangstoken, REST-Token, MCP-Token

Siehe auch: [Kiosk](#kiosk), [Gruppen und Rechte](#gruppen-und-rechte)

<a id="ausgleichswert"></a>
### Ausgleichswert

*Sensorik* — Ein Betrag in Kelvin, der je Thermostat von dessen Messung abgezogen wird, wenn es als Ersatzquelle dient.

Der Fühler eines Thermostats sitzt am Gerät selbst und kann deshalb vom Raum abweichen. Der Ausgleichswert wird in den Regelparametern der Zone je Thermostat eingetragen (ab Werk 0 K). Die Betriebsseite kann einen vorgeschlagenen Ausgleichswert aus dem Vergleich mit dem Wandfühler anzeigen; er gilt erst, wenn man ihn einträgt.

Auch: Temperaturausgleich, Ausgleich, vorgeschlagener Ausgleichswert

Siehe auch: [Ersatzquelle](#ersatzquelle), [Sensorkalibrierung](#sensorkalibrierung), [Echo-Regel](#echo-regel)

<a id="aussenkennlinie"></a>
### Außenkennlinie

*Sensorik* — Eine Tabelle für den Notbetrieb: zu jeder Außentemperatur eine Ein- und eine Aus-Dauer.

Sie besteht aus Zeilen mit Außentemperatur sowie Sekunden Ein und Aus; zwischen den Zeilen wird interpoliert. Eine Kennlinie braucht mindestens zwei Zeilen; die wärmste Zeile steht für „Aus“ und hat 0 Sekunden Ein. Eingestellt wird sie in den Regelvorgaben unter Notbetrieb bei Sensorausfall. Eine Alternative ohne Außentemperatur ist der Festtakt.

Auch: Kennlinie, Heizkurve im Notbetrieb

Siehe auch: [Notbetrieb](#notbetrieb), [Festtakt](#festtakt), [Außentemperatur](#aussentemperatur), [Wiederanlaufspanne](#wiederanlaufspanne)

<a id="aussentemperatur"></a>
### Außentemperatur

*Sensorik* — Die Temperatur draußen, aus einer anlagenweit gewählten Quelle.

Es gibt ein „draußen“ für die ganze Anlage, keinen Wert je Zone. Die Quelle wird in den Regelvorgaben unter Außentemperatur aus den bekannten Geräten gewählt. Der Wert wird unter anderem von der Außenkennlinie im Notbetrieb und vom Fenster-Alarm genutzt.

Auch: Außenfühler, Außenquelle, Außenwert, Außentemperaturquelle

Siehe auch: [Außenkennlinie](#aussenkennlinie), [Fenster-Alarm](#fenster-alarm), [Messquelle](#messquelle)

## B

<a id="bediengeraet"></a>
### Bediengerät

*Oberfläche* — Ein Gerät mit Tasten, etwa ein Wandtaster, dessen Tastendrücke Befehle für eine Zone auslösen können.

Die Seite Bediengeräte zeigt, welche Aktionen ein Gerät bisher geschickt hat; sie lassen sich dort Befehlen für eine Zone zuordnen. Welche Befehle zur Auswahl stehen, zeigt dieselbe Seite.

Auch: Bediengeräte, Wandtaster, Taste

Siehe auch: [Zone](#zone), [Sollwert-Modus](#sollwert-modus)

<a id="betriebsart"></a>
### Betriebsart

*Regelung* — Eine Einstellung je Zone: Automatik, Manuell oder Aus. „Aus“ ist nicht gleichbedeutend mit abgestellt, siehe Frostschutz.

Bei „Aus“ gilt der Frostschutz-Sollwert vor Übersteuerung, Urlaub und Zeitplan. Eingestellt wird die Betriebsart je Zone unter Zonen. Wie der Sollwert sonst zustande kommt, zeigt die Oberfläche als Begründung beim Sollwert.

Auch: Automatik, Manuell, Aus, Betriebsmodus

Siehe auch: [Sollwert](#sollwert), [Frostschutz](#frostschutz), [Übersteuerung](#uebersteuerung)

## E

<a id="echo-regel"></a>
### Echo-Regel

*Sensorik* — Soll verhindern, dass ein Thermostat als Ersatzquelle dient, dessen Messung nur ein Echo der vorgegebenen Raumtemperatur sein könnte.

Schreibt thermoctl einem selbstregelnden Thermostat eine Raumtemperatur, kann das Gerät diesen Wert als eigene Messung zurückmelden. Deshalb wird eine Messung erst dann als unabhängig behandelt, wenn sie mindestens 30 Minuten nach dem letzten Sendeversuch entstanden ist; als Sendeversuch zählt auch ein gescheiterter.

Auch: Echo, Echo-Erkennung, Echo-Prüfung

Siehe auch: [Ersatzquelle](#ersatzquelle), [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil), [Ausgleichswert](#ausgleichswert)

<a id="ersatzquelle"></a>
### Ersatzquelle

*Sensorik* — Eine Ersatzmessung für einen ausgefallenen Wandfühler, genommen von einem Thermostat der Zone.

Fällt der Wandfühler aus, kann die Messung eines Thermostats der Zone an seine Stelle treten, korrigiert um den Ausgleichswert. Welche Messung gilt, wie die Rückkehr zum Wandfühler geprüft wird und was geschieht, wenn auch die Ersatzquelle ausfällt, steht unter Notbetrieb und Rückkehrprüfung.

Auch: Thermostat-Ersatzquelle, Ersatzmessung

Siehe auch: [Notbetrieb](#notbetrieb), [Echo-Regel](#echo-regel), [Ausgleichswert](#ausgleichswert), [Rückkehrprüfung](#rueckkehrpruefung), [Messquelle](#messquelle)

## F

<a id="fenster-alarm"></a>
### Fenster-Alarm

*Sensorik* — Meldet ein vergessenes offenes Fenster, wenn es draußen kalt ist.

Er hängt an zwei Einstellungen: der Dauer, die ein Fenster offen stehen darf, und der Schwelle für die Außentemperatur. Beides steht in den Regelvorgaben unter Fenster-Alarm; ob gemeldet wird, stellt man unter Meldungen ein. Es ist eine Meldung; was die Regelung bei offenem Fenster tut, steht unter Fensterkontakt.

Auch: Fenster vergessen, Fenster vergessen offen

Siehe auch: [Fensterkontakt](#fensterkontakt), [Außentemperatur](#aussentemperatur), [Meldungen](#meldungen)

<a id="fenster-erkennung-temperatursturz"></a>
### Fenster-Erkennung aus Temperatursturz

*Regelung* — Vermutet ein offenes Fenster, wenn die Raumtemperatur schnell fällt, auch ohne Fensterkontakt.

Gedacht für Zonen ohne zugeordneten Fensterkontakt; sie wird je Zone in den Regelparametern aktiviert (ab Werk aus). Sturzschwelle und Zeitfenster stehen in den Regelvorgaben. Eine geöffnete Tür oder ein Luftzug kann dasselbe auslösen; die Schwellen sind begründete Schätzwerte.

Auch: Temperatursturz, Sturzschwelle, Fenster vermutlich offen

Siehe auch: [Fensterkontakt](#fensterkontakt), [Fenster-Alarm](#fenster-alarm)

<a id="fensterkontakt"></a>
### Fensterkontakt

*Regelung* — Ein Sensor, der meldet, ob ein Fenster offen ist.

Die Regelung berücksichtigt offene Fenster, kann aber je nach Einstellung und Zone abweichend reagieren; die Begründung jeder Entscheidung steht auf der Betriebsseite. Die Nachlaufzeit nach dem Schließen ist einstellbar (ab Werk 120 Sekunden); ob und wie sie wirkt, hängt von der Zone und ihren Geräten ab.

Auch: Fenster offen, Fenstersensor, Nachlauf nach Fensterschluss, Wiederanlauf nach Fenster

Siehe auch: [Frostschutz](#frostschutz), [Fenster-Alarm](#fenster-alarm), [Fenster-Erkennung aus Temperatursturz](#fenster-erkennung-temperatursturz), [Aktor](#aktor)

<a id="festhaengender-messwert"></a>
### Festhängender Messwert

*Sensorik* — Ein Sensor meldet zwar weiter, aber seit Stunden denselben Wert.

Als festhängend gilt ein Messwert, wenn alle Werte der letzten Stunden (einstellbar, ab Werk 12) nur minimal voneinander abweichen. Das ist kein Ausfall; es gibt einen Hinweis und optional eine Meldung. Ein stabiler Raum kann sich ebenfalls so verhalten.

Auch: festhängend, Messwert bewegt sich nicht mehr, Messwert unverändert

Siehe auch: [Sensor-Timeout](#sensor-timeout), [Meldungen](#meldungen)

<a id="festtakt"></a>
### Festtakt

*Sensorik* — Ein fester Zeittakt aus einer Ein- und einer Aus-Dauer für den Notbetrieb.

Der Festtakt gehört zum Notbetriebsprofil: eine Ein-Dauer und eine Aus-Dauer, beide einstellbar (ab Werk 10 Minuten Ein und 20 Minuten Aus). Wann statt seiner die Außenkennlinie gilt und wie der Notbetrieb abläuft, steht unter Notbetrieb.

Auch: Festtakt Ein, Festtakt Aus, fester Takt, Taktzyklus

Siehe auch: [Notbetrieb](#notbetrieb), [Außenkennlinie](#aussenkennlinie)

<a id="frostschutz"></a>
### Frostschutz

*Regelung* — Die niedrigste Temperatur, auf die ein Raum nicht fallen soll; sie soll Leitungen vor dem Einfrieren schützen.

Frostschutz ist ein eigener Sollwert-Modus. Er kommt unter anderem bei Betriebsart „Aus“ und bei fehlendem Zeitplan zur Anwendung; die Oberfläche nennt zu jedem Sollwert die Begründung. Die Sonnenabsenkung soll ihn nicht unterschreiten.

Auch: Frostschutz-Sollwert, Frostschutztemperatur, Frostschutzmodus

Siehe auch: [Sollwert](#sollwert), [Betriebsart](#betriebsart), [Sonnenabsenkung](#sonnenabsenkung), [Sensor-Timeout](#sensor-timeout), [Fensterkontakt](#fensterkontakt)

## G

<a id="gruppen-und-rechte"></a>
### Gruppen und Rechte

*Oberfläche* — Rechte hängen an Gruppen, nicht an Personen; die Gruppe legt auch das Oberflächen-Profil fest.

Das Oberflächen-Profil einer Gruppe (Anlage oder Wohnung) bestimmt, welche Oberfläche sie sieht; die Rechte regeln, was jemand darf. Beides wird bei den Gruppen eingestellt. Zonenbezogene Rechte lassen sich auf einzelne Zonen beschränken. Wie sich die Rechte mehrerer Gruppen verhalten, steht in docs/bedienung.md.

Auch: Gruppe, Rechte, Berechtigung, Oberflächen-Profil

Siehe auch: [Anlagensicht und Wohnungssicht](#oberflaechen), [API-Token](#api-token)

## H

<a id="heizzeit"></a>
### Heizzeit

*Betrieb* — Wie lange eine Zone an einem Tag eine Heizanforderung hatte.

Die Zahl gibt Entscheidungen der Regelung wieder, keine gemessene Wirkung im Raum, und ist weder Energie- noch Kostenmesser. Hinweise zur Deutung stehen auf der Seite Heizstatistik und in docs/bedienung.md im Abschnitt Heizzeit.

Auch: Heizstatistik

Siehe auch: [Regelentscheidung](#regelentscheidung), [Trockenlauf](#trockenlauf), [Relaisverschleiß](#relaisverschleiss)

<a id="home-assistant"></a>
### Home Assistant

*Oberfläche* — Eine Smart-Home-Zentrale, die sich optional per MQTT mit thermoctl verbinden lässt.

Die Anbindung ist freiwillig. Über MQTT meldet thermoctl seine Zonen an und nimmt Sollwerte und Betriebsarten entgegen. Die Entität „Regelung scharf“ zeigt die gespeicherte Freigabe, nicht den Zustand der Befehlsausgabe; den zeigt die Betriebsseite. Einzelheiten stehen unter Schnittstellen und in docs/mqtt.md.

Auch: HA, Home-Assistant-Add-on

Siehe auch: [MQTT und Broker](#mqtt-broker), [Homebridge](#homebridge), [Scharfschalten](#scharfschalten)

<a id="homebridge"></a>
### Homebridge

*Oberfläche* — Eine Brücke, die Zonen von thermoctl in Apple Home sichtbar macht.

Die Oberfläche erzeugt je Zone eine fertige Konfiguration für das Homebridge-Plugin mit den echten Topics der Anlage. Benutzername und Passwort darin sind Platzhalter; Homebridge sollte einen eigenen Broker-Zugang mit möglichst engen Rechten bekommen und nicht den von thermoctl (siehe docs/homebridge.md).

Auch: Apple Home, HomeKit

Siehe auch: [MQTT und Broker](#mqtt-broker), [Home Assistant](#home-assistant)

<a id="hysterese"></a>
### Hysterese

*Regelung* — Ein Spielraum um den Sollwert, der häufiges Wechseln der Heizanforderung vermeiden soll.

Die Hysterese legt fest, wie weit die Temperatur vom Sollwert abweichen muss, bevor die Hysterese-Regelung ihre Entscheidung wechselt. Der Wert gilt anlagenweit (ab Werk 0,3 K) und lässt sich je Zone abweichend einstellen. Weitere Regeln, etwa Mindestschaltdauer und Fensterkontakt, können vorgehen; die Begründung jeder Entscheidung steht auf der Betriebsseite.

Auch: Hysterese (K), Schalthysterese, Hysterese-Regelung

Siehe auch: [Mindestschaltdauer](#mindestschaltdauer), [PI-Regelung (Beta)](#pi-regelung), [Sollwert](#sollwert)

## K

<a id="kiosk"></a>
### Kiosk

*Oberfläche* — Eine Anzeige für ein Wandtablet, die ohne Benutzerkonto über ein eigenes Token geöffnet wird.

Ein Kiosk-Token hat einen Namen, eine Auswahl sichtbarer Zonen und optional die Erlaubnis zu bedienen. Es kann ein Ablaufdatum haben und lässt sich widerrufen. Die Adresse mit dem Token wird bei der Ausstellung angezeigt und gehört als Lesezeichen auf das Tablet; ein verlorenes Token ersetzt man durch ein neues.

Auch: Kiosk-Token, Wandtablet, Wandpanel, Tafel

Siehe auch: [API-Token](#api-token), [Anlagensicht und Wohnungssicht](#oberflaechen)

## M

<a id="meldungen"></a>
### Meldungen

*Betrieb* — Hinweise, die thermoctl bei Störungen an ein Ziel wie einen Webhook schicken kann.

Es gibt sechs Arten, die sich einzeln wählen lassen: Sensorstörung, Brücke oder Broker weg, gescheiterter Schaltbefehl, festhängender Messwert, Fenster vergessen offen und Problemmeldung aus einer Wohnung. Die Auswahl steht in den Regelvorgaben unter Meldungen; wohin eine Meldung geht, steht unter Schnittstellen.

Auch: Störungsmeldungen, Benachrichtigungen, Webhook, Testmeldung

Siehe auch: [Festhängender Messwert](#festhaengender-messwert), [Fenster-Alarm](#fenster-alarm), [MQTT und Broker](#mqtt-broker)

<a id="meross"></a>
### Meross

*Sensorik* — Eine Herstellerreihe von WLAN-Steckdosen, die thermoctl über deren Cloud ansprechen kann.

Meross-Steckdosen sind nicht über MQTT angebunden, sondern über die Cloud des Herstellers; dafür braucht thermoctl eine Anmeldung dort. Die Zugangsdaten werden in der Umgebung des Dienstes gesetzt, nicht in der Oberfläche; Einzelheiten stehen unter Schnittstellen und in docs/self-hosting.md.

Auch: Meross-Steckdose, Steckdose

Siehe auch: [Aktor](#aktor), [Zigbee2MQTT und Brücke](#zigbee2mqtt)

<a id="messquelle"></a>
### Messquelle

*Sensorik* — Der Temperatursensor, nach dem eine Zone regelt.

Die Messquelle (auch Wandfühler oder Raumfühler) wird bei den Geräten der Zone aus den bekannten Geräten mit Temperaturmessung gewählt. Was bei einem Ausfall geschieht, steht unter Sensor-Timeout, Ersatzquelle und Notbetrieb.

Auch: Wandfühler, Raumfühler, Fühler, Temperatursensor, Messquelle wählen

Siehe auch: [Ersatzquelle](#ersatzquelle), [Sensor-Timeout](#sensor-timeout), [Sensorkalibrierung](#sensorkalibrierung)

<a id="mindestschaltdauer"></a>
### Mindestschaltdauer

*Regelung* — Wie lange ein Zustand (Heizanforderung oder Aus) mindestens bestehen soll, bevor er wechseln darf.

Sie soll Geräte vor ständigem Wechsel schützen und gilt getrennt als Mindest-Einschaltdauer und Mindest-Ausschaltdauer. Ab Werk sind es je 5 Minuten, anlagenweit einstellbar und je Zone abweichend. Es gibt Ausnahmen; die Begründung jeder Entscheidung nennt, welche Regel gegriffen hat.

Auch: Mindest-Einschaltdauer, Mindest-Ausschaltdauer, Mindestdauer, Taktschutz

Siehe auch: [Hysterese](#hysterese), [PI-Regelung (Beta)](#pi-regelung), [Relaisverschleiß](#relaisverschleiss)

<a id="mqtt-broker"></a>
### MQTT und Broker

*Sensorik* — MQTT ist ein Nachrichtenweg zwischen Geräten und Diensten; der Broker ist dessen Vermittlungsstelle.

Sensoren, Zigbee2MQTT, Home Assistant, Homebridge und thermoctl können sich über einen MQTT-Broker austauschen. Fällt der Broker aus, können Messwerte ausbleiben; dafür gibt es eine eigene Meldung. Adresse und Zugangsdaten werden in der Umgebung des Dienstes gesetzt, nicht in der Oberfläche.

Auch: MQTT, Broker, MQTT-Broker

Siehe auch: [Zigbee2MQTT und Brücke](#zigbee2mqtt), [Home Assistant](#home-assistant), [Homebridge](#homebridge), [Meldungen](#meldungen)

## N

<a id="notbetrieb"></a>
### Notbetrieb

*Sensorik* — Ersatzverhalten, wenn für eine Zone keine brauchbare Temperaturmessung mehr vorliegt.

Der Ablauf kennt vier Stufen: Normal, Ersatzquelle, Notbetrieb und Rückkehrprüfung; nicht jede Zone durchläuft alle. Im Notbetrieb sollen Geräte mit Ein/Aus-Funktion nach einem Zeittakt angesprochen werden (Festtakt oder Außenkennlinie) und Thermostate den Notsollwert bekommen. Was bei einem bestimmten Gerät tatsächlich gesendet wird, hängt von Gerät und Einstellung ab und steht auf der Betriebsseite im Abschnitt Notbetrieb und im Schaltprotokoll. Für neu angelegte Zonen ist er ab Werk aktiv und lässt sich je Zone in den Regelparametern abstellen. Er ist eine Notlösung, keine Regelung.

Auch: Notbetrieb bei Sensorausfall, Sensorausfall, Notbetriebsprofil

Siehe auch: [Ersatzquelle](#ersatzquelle), [Festtakt](#festtakt), [Außenkennlinie](#aussenkennlinie), [Notsollwert](#notsollwert), [Rückkehrprüfung](#rueckkehrpruefung)

<a id="notsollwert"></a>
### Notsollwert

*Sensorik* — Die Zieltemperatur, die ein Thermostat im Notbetrieb bekommen soll.

Gilt anlagenweit (ab Werk 20 °C) und lässt sich je Zone überschreiben; ein leeres Feld erbt den Anlagenwert. Der Notsollwert betrifft Thermostate, nicht Geräte mit Ein/Aus-Schalter wie Fußbodenkreise. Ob die Übergabe an ein Thermostat gelungen ist, zeigt die Betriebsseite im Abschnitt Notbetrieb (Spalten Übergabe und Rückstellung) und das Schaltprotokoll.

Auch: Anlagenweiter Notsollwert, Eigener Notsollwert

Siehe auch: [Notbetrieb](#notbetrieb), [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil)

## P

<a id="passkey"></a>
### Passkey

*Oberfläche* — Eine Anmeldung ohne Passwort, bei der ein geheimer Schlüssel das Gerät nicht verlässt.

Ein Passkey ist an die Adresse der Seite gebunden; das soll die Eingabe auf nachgemachten Seiten verhindern. Er braucht einen Browser mit Passkey-Unterstützung und eine verschlüsselte Verbindung. Verwaltet wird er unter Mehr, Passkeys.

Auch: Passkeys, WebAuthn

Siehe auch: [Gruppen und Rechte](#gruppen-und-rechte), [API-Token](#api-token)

<a id="pi-regelung"></a>
### PI-Regelung (Beta)

*Regelung* — Eine optionale, experimentelle Regelart (Beta); siehe Regelparameter der Zone.

Statt zwischen zwei Schwellen zu wechseln, berechnet der PI-Regler aus der Abweichung einen Tastgrad: den Anteil einer Zeitspanne mit Heizanforderung. Sie wird je Zone in den Regelparametern eingestellt (ab Werk aus). Dort steht auch der Hinweis zum Relaisverschleiß; Reglertyp und ein etwaiger Rückfallgrund stehen auf der Betriebsseite.

Auch: PI, PI-Regler, Tastgrad, Verstärkung Kp, Nachstellzeit Ti, Beta

Siehe auch: [Hysterese](#hysterese), [Relaisverschleiß](#relaisverschleiss), [Mindestschaltdauer](#mindestschaltdauer)

## R

<a id="regelentscheidung"></a>
### Regelentscheidung

*Regelung* — Das Ergebnis eines Regelzyklus für eine Zone: Heizanforderung oder nicht, mit Begründung.

Zu einer Entscheidung wird in der Regel eine Begründung genannt, zum Beispiel die Temperatur, ein offenes Fenster oder die Mindestdauer. Sie steht auf der Betriebsseite unter Was die Regelung gerade entscheidet. Die Oberfläche spricht von Heizanforderung und, im Trockenlauf, von Schattenentscheidung.

Auch: Entscheidung, Begründung, Schattenentscheidung, Heizanforderung

Siehe auch: [Regelzyklus](#regelzyklus), [Hysterese](#hysterese), [Trockenlauf](#trockenlauf), [Schattenbetrieb](#schattenbetrieb)

<a id="regelzyklus"></a>
### Regelzyklus

*Regelung* — Der Takt, in dem die Regelung für die Zonen neu entscheidet.

Im Regelzyklus wird für die Zonen der Sollwert ermittelt und über die Heizanforderung entschieden. Die Dauer ist anlagenweit einstellbar, ab Werk 60 Sekunden. Sie ist auch die Zeiteinheit des Aktiv-Bereitschafts-Verbunds.

Auch: Regelzyklus (Sekunden), Zyklus, Regelintervall

Siehe auch: [Regelentscheidung](#regelentscheidung), [Aktiv-Bereitschafts-Verbund](#aktiv-bereitschafts-verbund)

<a id="relaisverschleiss"></a>
### Relaisverschleiß

*Betrieb* — Schätzt aus den gesendeten Befehlen, wie stark Relais beansprucht werden, und rechnet das auf ein Jahr hoch.

Gezählt werden Wechsel zwischen gesendeten Ein- und Aus-Befehlen, nicht beobachtete Relaisbewegungen. Verglichen wird mit einer angenommenen Lebensdauer (ab Werk 500.000 Schaltspiele): ein austauschbarer Vergleichswert, keine Herstellerangabe. Welche Befehle im Einzelnen zählen, steht auf der Seite Relaisverschleiß.

Auch: Schaltspiel, Schaltspiele, Relais-Lebensdauer, Jahreshochrechnung

Siehe auch: [PI-Regelung (Beta)](#pi-regelung), [Hysterese](#hysterese), [Mindestschaltdauer](#mindestschaltdauer), [Schaltprotokoll](#schaltprotokoll)

<a id="rueckkehrpruefung"></a>
### Rückkehrprüfung

*Sensorik* — Die Probezeit, nach der eine Zone nach einem Ausfall wieder einer Quelle vertraut: dem Wandfühler oder der Ersatzquelle.

Geprüft wird jeweils eine Quelle. Sie wird beim Beginn der Prüfung gewählt (der Wandfühler, wenn er dann wieder liefert, sonst die Ersatzquelle) und bleibt danach an diese Prüfung gebunden. Wie viele neue Messwerte nötig sind und wie lange die Quelle durchgehend brauchbar sein muss, ist im Notbetriebsprofil einstellbar (Regelvorgaben). Den Ablauf beschreibt docs/bedienung.md im Abschnitt Notbetrieb bei Sensorausfall.

Auch: Rückkehr, Rückkehr: Mindestdauer stabil, Rückkehr: nötige Messungen

Siehe auch: [Notbetrieb](#notbetrieb), [Ersatzquelle](#ersatzquelle), [Messquelle](#messquelle)

## S

<a id="schaltprotokoll"></a>
### Schaltprotokoll

*Betrieb* — Das Journal der Befehle an Geräte und ihrer Ergebnisse.

Ein Eintrag kann Zeitpunkt, Zone, Gerät, Befehl, Ergebnis und eine Begründung enthalten; welche Angaben vorliegen, zeigt die Seite. Mögliche Ergebnisse sind zum Beispiel ausgeführt, unterdrückt und gescheitert; dazu kommen gekennzeichnete Einträge zu Entscheidungen des Notbetriebs. Auf der Seite Schaltprotokoll lässt sich nachlesen, was zu einem Eintrag gespeichert ist.

Auch: Befehlsprotokoll, Befehle, Schaltbefehl

Siehe auch: [Trockenlauf](#trockenlauf), [Notbetrieb](#notbetrieb), [Relaisverschleiß](#relaisverschleiss)

<a id="scharfschalten"></a>
### Scharfschalten

*Betrieb* — Die Freigabe dafür, dass die Regelung Befehle an Geräte senden darf.

Dafür gibt es zwei Riegel. Der erste ist die gespeicherte Freigabe auf der Betriebsseite (mit Begründung). Der zweite wird beim Start des Dienstes aus dieser Freigabe gebildet und gilt für alle Wege, auf denen Befehle hinausgehen können, MQTT ebenso wie Meross. Nach dem Scharfschalten braucht es deshalb einen Neustart; bis dahin zeigt die Oberfläche „Scharf, Neustart fehlt“. Den Ablauf beschreibt docs/scharfschalten.md.

Auch: scharf, Scharfschaltung, Riegel, MQTT-Riegel, Freigabe, Neustart fehlt

Siehe auch: [Trockenlauf](#trockenlauf), [Schattenbetrieb](#schattenbetrieb), [Aktor](#aktor)

<a id="schattenbetrieb"></a>
### Schattenbetrieb

*Betrieb* — Die Anlaufphase, in der thermoctl mitentscheidet und protokolliert, damit sich seine Entscheidungen mit der bisherigen Steuerung vergleichen lassen.

Technisch ist es derselbe Zustand wie der Trockenlauf. Die Aufbewahrungsfrist für Schattenentscheidungen ist einstellbar (ab Werk 365 Tage). Wie der Vergleich mit der bisherigen Steuerung abläuft, beschreibt docs/inbetriebnahme-schattenbetrieb.md.

Auch: Schattenprotokoll, Schattenentscheidungen, Schattenlauf

Siehe auch: [Trockenlauf](#trockenlauf), [Scharfschalten](#scharfschalten), [Regelentscheidung](#regelentscheidung)

<a id="selbstregelndes-thermostatventil"></a>
### Selbstregelndes Thermostatventil

*Betrieb* — Ein Thermostat, das mit einer vorgegebenen Zieltemperatur selbst regelt.

Bei einem selbstregelnden Thermostat gibt thermoctl im Normalbetrieb eine Zieltemperatur vor und, wenn das Gerät es annimmt, die Raumtemperatur; das Gerät regelt dann selbst. Ob ein Gerät selbstregelnd ist, wird bei den Geräten der Zone festgelegt. Ein nicht selbstregelndes Gerät behandelt thermoctl dagegen wie einen Schalter mit Ein und Aus. Wie das Gerät im Notbetrieb behandelt wird, steht unter Notbetrieb.

Auch: selbstregelnd, Thermostat

Siehe auch: [Aktor](#aktor), [Notsollwert](#notsollwert), [Echo-Regel](#echo-regel), [Hysterese](#hysterese)

<a id="sensor-timeout"></a>
### Sensor-Timeout

*Sensorik* — Die Zeit ohne neuen Messwert, nach der ein Sensor als ausgefallen („veraltet“) gilt.

Ab Werk 30 Minuten, anlagenweit einstellbar und je Zone abweichend. Danach gilt die Messung als veraltet. Was dann geschieht, hängt von der Konfiguration ab (Ersatzquelle, Notbetrieb, Frostschutz); die Begründung der Entscheidung steht auf der Betriebsseite.

Auch: Sensor gilt als ausgefallen nach, veraltet, Messwert veraltet, keine Quelle

Siehe auch: [Messquelle](#messquelle), [Ersatzquelle](#ersatzquelle), [Notbetrieb](#notbetrieb), [Frostschutz](#frostschutz), [Festhängender Messwert](#festhaengender-messwert)

<a id="sensorkalibrierung"></a>
### Sensorkalibrierung

*Sensorik* — Ein fester Betrag in Kelvin, der auf die Messung eines Sensors aufgerechnet wird.

Der Wert wird zur gemessenen Temperatur addiert, um eine bekannte Abweichung des Sensors auszugleichen. Negative Werte sind erlaubt. Er wird je Zone in den Regelparametern eingestellt. Nicht zu verwechseln mit dem Ausgleichswert eines Thermostats, der von dessen Messung abgezogen wird.

Auch: Temperaturkorrektur, Temperaturversatz, Offset, Kalibrierung

Siehe auch: [Ausgleichswert](#ausgleichswert), [Messquelle](#messquelle)

<a id="sollwert"></a>
### Sollwert

*Regelung* — Die Temperatur, die ein Raum gerade erreichen soll.

Der Sollwert ergibt sich aus mehreren Quellen: Betriebsart, Übersteuerung, Urlaub, Zeitplan und Frostschutz; die Sonnenabsenkung kann ihn zusätzlich beeinflussen. Welche Quelle gerade gilt, nennt die Oberfläche als Begründung bei jedem Sollwert.

Auch: Soll, Zieltemperatur, Solltemperatur, Sollwert mit Begründung

Siehe auch: [Sollwert-Modus](#sollwert-modus), [Zeitplan](#zeitplan), [Übersteuerung](#uebersteuerung), [Urlaub](#urlaub), [Frostschutz](#frostschutz), [Sonnenabsenkung](#sonnenabsenkung)

<a id="sollwert-modus"></a>
### Sollwert-Modus

*Regelung* — Eine benannte Temperaturstufe wie Komfort, Tag, Nacht oder Frostschutz.

Je Zone wird jedem Modus eine Temperatur zugeordnet; ein leeres Feld erbt den anlagenweiten Vorgabewert. Der Zeitplan wechselt zu bestimmten Uhrzeiten zwischen den Modi, sodass sich eine Temperatur an einer Stelle ändern lässt, statt in jedem Schaltpunkt.

Auch: Modus, Modi, Komfort, Tag, Nacht, Sollwertmodus

Siehe auch: [Zeitplan](#zeitplan), [Sollwert](#sollwert), [Frostschutz](#frostschutz)

<a id="sonnenabsenkung"></a>
### Sonnenabsenkung

*Regelung* — Kann den Sollwert absenken, wenn bald Sonne zu erwarten ist.

Grundlage ist die Wettervorhersage: Bei einer vorhergesagten Einstrahlung von mindestens 120 W/m² in den nächsten Stunden (einstellbar, ab Werk 3) kann der Sollwert einer Zone um bis zu ihre Obergrenze (ab Werk 2 K) sinken, gewichtet mit dem Sonnenprofil der Zone von 0 bis 1. Der Frostschutz-Sollwert soll dabei nicht unterschritten werden. Die Funktion braucht einen Standort und eine erreichbare Vorhersage.

Auch: Sonnenprofil, Sonnenabsenkung, Obergrenze, Solarabsenkung, Sonnenvorhersage

Siehe auch: [Frostschutz](#frostschutz), [Sollwert](#sollwert)

## T

<a id="trockenlauf"></a>
### Trockenlauf

*Betrieb* — Die Regelung entscheidet und protokolliert; Befehle an Geräte sollen dabei nicht hinausgehen.

Das ist der Zustand nach der Einrichtung. Entscheidungen und Begründungen werden aufgezeichnet, damit man sie über mehrere Tage beobachten kann, bevor das Scharfschalten erfolgt. Was im Einzelnen gesendet wird, beschreibt docs/scharfschalten.md; siehe auch Schattenbetrieb.

Auch: unscharf, Trockenlauf-Modus, im Trockenlauf unterdrückt

Siehe auch: [Scharfschalten](#scharfschalten), [Schattenbetrieb](#schattenbetrieb), [Schaltprotokoll](#schaltprotokoll)

## U

<a id="uebersteuerung"></a>
### Übersteuerung

*Regelung* — Eine vorübergehend von Hand gesetzte Temperatur, die den Zeitplan überlagert.

Sie gilt bis zu einem gewählten Ende oder bis auf Widerruf; der Zeitplan selbst wird dadurch nicht verändert. Ein Boost ist eine Übersteuerung, ebenso die Abwesenheit je Raum. Wie sie sich zu Urlaub, Zeitplan und Betriebsart verhält, zeigt die Oberfläche als Begründung beim Sollwert.

Auch: Boost, Für eine Weile wärmer, Übersteuern, Override

Siehe auch: [Sollwert](#sollwert), [Zeitplan](#zeitplan), [Abwesenheit](#abwesenheit), [Betriebsart](#betriebsart)

<a id="urlaub"></a>
### Urlaub

*Regelung* — Eine anlagenweite Absenkung für einen Zeitraum, gesetzt von der Verwaltung.

Die Verwaltung setzt ersten und letzten Tag sowie die Absenktemperatur für die ganze Anlage. Ob und wie der Urlaub bei einer Zone wirkt, hängt von weiteren Einstellungen ab, etwa Betriebsart, Übersteuerung und Frostschutz der Zone; die Begründung beim Sollwert nennt die gerade geltende Quelle. Zu unterscheiden von der Abwesenheit einzelner Bewohner.

Auch: Urlaubsbetrieb, Absenkwert, Urlaub geplant, Urlaub läuft

Siehe auch: [Abwesenheit](#abwesenheit), [Übersteuerung](#uebersteuerung), [Sollwert](#sollwert)

## V

<a id="ventilschutz"></a>
### Ventilschutz

*Regelung* — Ein Schutzlauf, der dem Festsitzen nach langem Stillstand vorbeugen soll.

Der Schutzlauf wird nach einem einstellbaren Abstand in Tagen und für eine einstellbare Dauer in Minuten angefordert. Er wird je Zone in den Regelparametern eingestellt (ab Werk aus). Wann er tatsächlich ausgeführt wird, hängt von weiteren Bedingungen ab; die Begründung der Entscheidung steht auf der Betriebsseite.

Auch: Schutzlauf

Siehe auch: [Mindestschaltdauer](#mindestschaltdauer), [Trockenlauf](#trockenlauf), [Aktor](#aktor)

## W

<a id="wiederanlaufspanne"></a>
### Wiederanlaufspanne

*Sensorik* — Eine Spanne in Kelvin am oberen Ende der Außenkennlinie, die häufiges Neuanlaufen im Notbetrieb vermeiden soll.

Sie gehört zum Notbetriebsprofil (Regelvorgaben, ab Werk 1 K). Sie soll verhindern, dass ein Wert um den obersten Punkt der Kennlinie ständig zwischen Ein und Aus wechselt. Eine kurze Erklärung der Wirkung steht in docs/bedienung.md im Abschnitt Notbetrieb bei Sensorausfall.

Auch: Wiederanlaufspanne (K)

Siehe auch: [Außenkennlinie](#aussenkennlinie), [Notbetrieb](#notbetrieb), [Hysterese](#hysterese)

## Z

<a id="zeitplan"></a>
### Zeitplan

*Regelung* — Der Wochenplan einer Zone: Schaltpunkte, die zu bestimmten Zeiten den Sollwert-Modus wechseln.

Ein Schaltpunkt gilt bis zum nächsten, Woche für Woche. Solange kein Schaltpunkt eingetragen ist, kommt der Frostschutz zur Anwendung. Die Vorschau zeigt auch eine gerade laufende Übersteuerung, nicht nur den gemalten Plan.

Auch: Wochenplan, Schaltpunkt, Schaltpunkte, Schaltzeit, Heizzeit-Plan

Siehe auch: [Sollwert-Modus](#sollwert-modus), [Sollwert](#sollwert), [Übersteuerung](#uebersteuerung), [Frostschutz](#frostschutz)

<a id="zigbee2mqtt"></a>
### Zigbee2MQTT und Brücke

*Sensorik* — Die Software, die Zigbee-Funkgeräte über MQTT zugänglich macht; die Brücke ist ihre Verbindung.

Funksensoren, Thermostate und Schalter hängen an der Zigbee2MQTT-Brücke. Fällt die Brücke aus, können Messwerte mehrerer Geräte gleichzeitig ausbleiben; dafür gibt es eine eigene Meldung. Neue Geräte können in der Geräteliste erscheinen und werden von Hand einer Zone zugeordnet.

Auch: Zigbee, Zigbee-Brücke, Brücke, Z2M

Siehe auch: [MQTT und Broker](#mqtt-broker), [Messquelle](#messquelle), [Aktor](#aktor)

<a id="zone"></a>
### Zone

*Betrieb* — Ein Raum oder eine Gruppe von Räumen mit eigenem Sollwert.

Einer Zone sind eine Messquelle, Geräte, ein Zeitplan, Sollwerte und Regelparameter zugeordnet. Ohne mindestens eine Zone gibt es nichts zu regeln. Was die Zone nicht selbst einstellt, erbt sie aus den anlagenweiten Regelvorgaben.

Auch: Raum, Zonen

Siehe auch: [Messquelle](#messquelle), [Aktor](#aktor), [Zeitplan](#zeitplan), [Anlagensicht und Wohnungssicht](#oberflaechen)
