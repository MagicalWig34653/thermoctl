# Glossar

<!-- Diese Datei wird erzeugt. Nicht von Hand ändern: Quelle ist thermoctl/data/glossar.json, Erzeugung mit `python -m tools.glossar_erzeugen`. -->

Die Fachbegriffe der Oberfläche in einfachen Worten. Dieselben Erklärungen stehen in der Weboberfläche unter `/glossar`, und die kleinen Fragezeichen neben Einstellungen führen direkt hierher.

**Bereiche:** Regelung · Sensorik · Betrieb · Oberfläche — der Bereich steht unter jedem Begriff.

[A](#a) · [B](#b) · [E](#e) · [F](#f) · [G](#g) · [H](#h) · [K](#k) · [M](#m) · [N](#n) · [P](#p) · [R](#r) · [S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w) · [Z](#z)

## A

<a id="abwesenheit"></a>
### Abwesenheit

*Regelung* — Hält alle eigenen Räume bis zu einem Rückkehrdatum auf einer gewählten, meist sparsameren Temperatur.

Die Abwesenheit setzt ein Bewohner in der Wohnungssicht für seine eigenen Räume. Sie ist eine Übersteuerung mit gemeinsamem Ende; der Wochenplan bleibt unverändert und gilt danach wieder. Nicht zu verwechseln mit dem anlagenweiten Urlaub, den die Verwaltung setzt.

Auch: Zurück am

Siehe auch: [Urlaub](#urlaub), [Übersteuerung](#uebersteuerung), [Anlagensicht und Wohnungssicht](#oberflaechen)

<a id="aktiv-bereitschafts-verbund"></a>
### Aktiv-Bereitschafts-Verbund

*Betrieb* — Mehrere Instanzen teilen sich eine Datenbank; nur eine regelt, die andere springt bei Ausfall ein.

Die führende Instanz erneuert regelmäßig ihre Zuständigkeit. Bleibt das mehrere Regelzyklen lang aus (einstellbar, ab Werk 5), übernimmt die Bereitschaftsinstanz. Eine Instanz in Bereitschaft regelt und schaltet nichts, lässt sich aber ansehen und konfigurieren.

Auch: Verbund, Bereitschaft, Führend

Siehe auch: [Regelzyklus](#regelzyklus), [Scharfschalten](#scharfschalten)

<a id="aktor"></a>
### Aktor

*Betrieb* — Ein Gerät, das die Heizung tatsächlich schaltet oder einstellt.

Aktoren sind Schaltsteckdosen und Relais (Ein/Aus), aber auch Thermostatventile (Zieltemperatur). Welcher Zone ein Aktor angehört und ob er sich selbst regelt, wird bei den Geräten der Zone festgelegt. Ob thermoctl einem Aktor wirklich Befehle schickt, entscheidet der Trockenlauf bzw. das Scharfschalten; im Trockenlauf wird nur protokolliert. Wie ein Aktor im Notbetrieb behandelt wird, hängt von seinen Fähigkeiten ab (Ein/Aus oder Thermostat).

Auch: Schaltaktor, Stellglied, Schalter

Siehe auch: [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil), [Trockenlauf](#trockenlauf), [Zone](#zone)

<a id="oberflaechen"></a>
### Anlagensicht und Wohnungssicht

*Oberfläche* — Zwei Oberflächen: eine für Verwaltung und Technik, eine für Bewohner.

Die Gruppe eines Benutzers entscheidet, welche er sieht. Die Anlagensicht zeigt die ganze Anlage mit Geräten, Regelparametern und Protokollen. Die Wohnungssicht zeigt nur die eigenen Räume ohne Fachbegriffe: Temperatur, Wochenplan, Heizzeit und Abwesenheit.

Auch: Anlagensicht, Wohnungssicht, Mietersicht, Profil

Siehe auch: [Gruppen und Rechte](#gruppen-und-rechte), [Abwesenheit](#abwesenheit), [Kiosk](#kiosk)

<a id="api-token"></a>
### API-Token

*Oberfläche* — Ein Zugangsschlüssel für Programme, die thermoctl über die Schnittstellen bedienen.

Ein Token kann höchstens, was sein Aussteller selbst darf; verliert der Aussteller später ein Recht, behält auch das Token es nicht. Der Klartext erscheint genau einmal; gespeichert wird nur ein Hash. Wer ihn verliert, stellt ein neues Token aus.

Auch: Token, Zugangstoken, REST-Token, MCP-Token

Siehe auch: [Kiosk](#kiosk), [Gruppen und Rechte](#gruppen-und-rechte)

<a id="ausgleichswert"></a>
### Ausgleichswert

*Sensorik* — Gleicht aus, um wie viel Kelvin ein Thermostat zu warm misst, wenn es als Ersatzquelle dient.

Der Fühler eines Thermostatventils sitzt am Heizkörper und misst meist wärmer als der Raum. Der je Thermostat eingetragene Wert wird von dessen Messung abgezogen, ab Werk 0 K. Die Betriebsseite zeigt dazu einen vorgeschlagenen Ausgleichswert aus dem Vergleich mit dem Wandfühler; übernommen wird er nur, wenn man ihn einträgt.

Auch: Temperaturausgleich, Ausgleich, vorgeschlagener Ausgleichswert

Siehe auch: [Ersatzquelle](#ersatzquelle), [Sensorkalibrierung](#sensorkalibrierung), [Echo-Regel](#echo-regel)

<a id="aussenkennlinie"></a>
### Außenkennlinie

*Sensorik* — Legt im Notbetrieb fest, wie lange ein Fußbodenkreis je nach Außentemperatur ein- und ausgeschaltet bleibt.

Sie besteht aus Zeilen mit Außentemperatur sowie Sekunden Ein und Aus; zwischen den Zeilen wird interpoliert. Eine Kennlinie braucht mindestens zwei Zeilen, und genau die wärmste hat 0 Sekunden Ein: der Punkt, ab dem nicht mehr geheizt wird. Ohne Kennlinie oder ohne brauchbaren Außenwert gilt der Festtakt.

Auch: Kennlinie, Heizkurve im Notbetrieb

Siehe auch: [Notbetrieb](#notbetrieb), [Festtakt](#festtakt), [Außentemperatur](#aussentemperatur), [Wiederanlaufspanne](#wiederanlaufspanne)

<a id="aussentemperatur"></a>
### Außentemperatur

*Sensorik* — Die Temperatur draußen, gemessen von einer einzigen anlagenweit gewählten Quelle.

Es gibt genau ein „draußen“ für die ganze Anlage, kein Wert je Zone. Die Quelle wird wie die Messquelle einer Zone aus den bekannten Geräten gewählt. Der Wert steuert die Außenkennlinie im Notbetrieb und den Fenster-Alarm; meldet die Quelle nicht mehr, gilt er als veraltet.

Auch: Außenfühler, Außenquelle, Außenwert, Außentemperaturquelle

Siehe auch: [Außenkennlinie](#aussenkennlinie), [Fenster-Alarm](#fenster-alarm), [Messquelle](#messquelle)

## B

<a id="bediengeraet"></a>
### Bediengerät

*Oberfläche* — Ein Gerät mit Tasten, etwa ein Wandtaster, dessen Tastendrücke Befehle für eine Zone auslösen.

thermoctl rät nicht, welche Tasten ein Modell hat, sondern zeigt, welche Aktionen das Gerät tatsächlich geschickt hat; diese lassen sich Befehlen zuordnen. Erlaubt sind bewusst wenige Befehle: wärmer, kälter, zur nächsten Schaltzeit springen, aus, automatisch.

Auch: Bediengeräte, Wandtaster, Taste

Siehe auch: [Zone](#zone), [Sollwert-Modus](#sollwert-modus)

<a id="betriebsart"></a>
### Betriebsart

*Regelung* — Automatik, Manuell oder Aus; „Aus“ heißt Frostschutz, nicht abgeschaltet.

Bei „Aus“ hält die Zone nur den Frostschutz-Sollwert; Übersteuerung, Urlaub und Zeitplan wirken dann nicht. „Automatik“ und „Manuell“ unterscheiden sich bei der Ermittlung des Sollwerts nicht: Es gilt die Vorrangfolge, die unter Sollwert steht.

Auch: Automatik, Manuell, Aus, Betriebsmodus

Siehe auch: [Sollwert](#sollwert), [Frostschutz](#frostschutz), [Übersteuerung](#uebersteuerung)

## E

<a id="echo-regel"></a>
### Echo-Regel

*Sensorik* — Ein Thermostat, dem thermoctl die Raumtemperatur vorgibt, misst nur sein eigenes Echo und taugt dann nicht als Ersatzquelle.

Schreibt thermoctl einem selbstregelnden Thermostat eine Raumtemperatur, gibt es diese Zahl binnen Sekunden als „eigene“ Messung zurück. Als unabhängig zählt seine Messung erst, wenn sie mindestens 30 Minuten nach dem letzten Schreiben entstanden ist; ältere Werte bleiben ausgeschlossen.

Auch: Echo, Echo-Erkennung, Echo-Prüfung

Siehe auch: [Ersatzquelle](#ersatzquelle), [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil), [Ausgleichswert](#ausgleichswert)

<a id="ersatzquelle"></a>
### Ersatzquelle

*Sensorik* — Ersatzmessung für einen ausgefallenen Wandfühler, genommen von einem Thermostat der Zone.

Fällt der Wandfühler aus und ist ein Thermostat der Zone brauchbar, gilt dessen Messung: die kälteste brauchbare Messung der zugeordneten Thermostate, korrigiert um den jeweiligen Ausgleichswert. Die Zone regelt damit weiter nach der Hysterese; eine PI-Regelung ist solange ausgesetzt. Ein Thermostat, das nur sein Echo liefert, zählt nicht. Meldet sich der Wandfühler wieder, übernimmt er erst nach der Rückkehrprüfung. Fällt auch die Ersatzquelle aus, beginnt der Notbetrieb.

Auch: Thermostat-Ersatzquelle, Ersatzmessung

Siehe auch: [Notbetrieb](#notbetrieb), [Echo-Regel](#echo-regel), [Ausgleichswert](#ausgleichswert), [Rückkehrprüfung](#rueckkehrpruefung), [Messquelle](#messquelle)

## F

<a id="fenster-alarm"></a>
### Fenster-Alarm

*Sensorik* — Meldet ein vergessenes offenes Fenster, wenn es draußen kalt ist.

Beide Bedingungen müssen zugleich gelten: Das Fenster einer Zone steht länger als die eingestellte Dauer offen, und die Außentemperatur liegt unter der eingestellten Schwelle. Ist die Außentemperatur oder der Fensterzustand gerade unbekannt, wird weder gemeldet noch entwarnt. Es ist eine Meldung und kein Eingriff; was die Heizung bei offenem Fenster tut, regeln Fensterkontakt und Frostschutz.

Auch: Fenster vergessen, Fenster vergessen offen

Siehe auch: [Fensterkontakt](#fensterkontakt), [Außentemperatur](#aussentemperatur), [Meldungen](#meldungen)

<a id="fenster-erkennung-temperatursturz"></a>
### Fenster-Erkennung aus Temperatursturz

*Regelung* — Vermutet ein offenes Fenster, wenn die Raumtemperatur schnell fällt, auch ohne Fensterkontakt.

Nur wirksam für eine Zone ohne zugeordneten Fensterkontakt und mit eingeschaltetem Schalter in den Regelparametern (ab Werk aus). Fällt die Temperatur innerhalb des Zeitfensters mindestens um die Sturzschwelle, behandelt sie die Zone wie bei offenem Fenster und kennzeichnet das als Vermutung. Eine geöffnete Tür oder ein Luftzug kann dasselbe auslösen; die Schwellen sind begründete Schätzwerte.

Auch: Temperatursturz, Sturzschwelle, Fenster vermutlich offen

Siehe auch: [Fensterkontakt](#fensterkontakt), [Fenster-Alarm](#fenster-alarm)

<a id="fensterkontakt"></a>
### Fensterkontakt

*Regelung* — Ein Sensor, der meldet, ob ein Fenster offen ist; bei offenem Fenster fordert die Regelung in der Regel kein Heizen an.

Bei offenem Fenster fordert die Regelung in der Regel kein Heizen an. Ausnahme: Fällt der Raum um mehr als die Hysterese unter den Frostschutz-Sollwert, wird trotzdem geheizt. Nach dem Schließen wartet die Regelung die Nachlaufzeit ab (einstellbar, ab Werk 120 Sekunden), bevor sie wieder heizen darf. Zonen, in denen kein Aktor als selbstregelnd eingestellt ist (reine Ein/Aus-Aktoren), werden durch ein offenes Fenster nicht abgeschaltet, weil eine solche Fußbodenheizung dafür zu träge ist. Im Notbetrieb läuft der Takt unabhängig vom Fenster weiter.

Auch: Fenster offen, Fenstersensor, Nachlauf nach Fensterschluss, Wiederanlauf nach Fenster

Siehe auch: [Frostschutz](#frostschutz), [Fenster-Alarm](#fenster-alarm), [Fenster-Erkennung aus Temperatursturz](#fenster-erkennung-temperatursturz), [Aktor](#aktor)

<a id="festhaengender-messwert"></a>
### Festhängender Messwert

*Sensorik* — Ein Sensor meldet zwar weiter, aber seit Stunden immer denselben Wert.

Als festhängend gilt ein Messwert, wenn alle Werte der letzten Stunden (einstellbar, ab Werk 12) nur minimal voneinander abweichen. Das ist kein Ausfall: Die Zone regelt unverändert mit dem Wert weiter, es gibt nur einen Hinweis und optional eine Meldung. Ein ungeheizter, stabiler Raum kann sich ebenfalls so verhalten.

Auch: festhängend, Messwert bewegt sich nicht mehr, Messwert unverändert

Siehe auch: [Sensor-Timeout](#sensor-timeout), [Meldungen](#meldungen)

<a id="festtakt"></a>
### Festtakt

*Sensorik* — Im Notbetrieb schaltet ein Fußbodenkreis nach der Uhr abwechselnd ein und aus, unabhängig von der Raumtemperatur.

Der Festtakt gibt an, wie lange ein Kreis je Zyklus eingeschaltet und wie lange danach ausgeschaltet bleibt, jeweils mindestens so lange wie die Mindestschaltdauer der Zone. Ab Werk sind es 10 Minuten Ein und 20 Minuten Aus; beides ist einstellbar. Der Takt beginnt mit der Aus-Phase. Ausnahme: War der Aktor beim Eintritt schon eingeschaltet, bleibt er zunächst für seine verbleibende Mindest-Einschaltdauer an. Mit Außenkennlinie und brauchbarem Außenwert hat die Kennlinie Vorrang.

Auch: Festtakt Ein, Festtakt Aus, fester Takt, Taktzyklus

Siehe auch: [Notbetrieb](#notbetrieb), [Außenkennlinie](#aussenkennlinie)

<a id="frostschutz"></a>
### Frostschutz

*Regelung* — Die niedrigste Temperatur, auf die ein Raum nicht fallen soll; sie schützt Leitungen vor dem Einfrieren.

Frostschutz ist ein eigener Sollwert-Modus. Er gilt bei Betriebsart „Aus“, solange kein Zeitplan greift (etwa weil noch kein Schaltpunkt eingetragen ist), und bei Sensorausfall, wenn die Zone dem letzten Messwert nicht mehr traut; ist der Notbetrieb aktiv, bestimmt für die Aktoren stattdessen dessen Ablauf. Auch bei offenem Fenster wird geheizt, wenn der Raum um mehr als die Hysterese darunter fällt. Die Sonnenabsenkung geht nie unter diese Grenze.

Auch: Frostschutz-Sollwert, Frostschutztemperatur, Frostschutzmodus

Siehe auch: [Sollwert](#sollwert), [Betriebsart](#betriebsart), [Sonnenabsenkung](#sonnenabsenkung), [Sensor-Timeout](#sensor-timeout), [Fensterkontakt](#fensterkontakt)

## G

<a id="gruppen-und-rechte"></a>
### Gruppen und Rechte

*Oberfläche* — Rechte hängen an Gruppen, nicht an Personen; die Gruppe legt auch die Oberfläche fest.

Wer in mehreren Gruppen ist, darf alles, was mindestens eine erlaubt. Zonenbezogene Rechte lassen sich auf einzelne Zonen beschränken. Das Oberflächen-Profil einer Gruppe (Anlage oder Wohnung) bestimmt nur, welche Seiten angezeigt werden; was jemand darf, steht allein in den Rechten.

Auch: Gruppe, Rechte, Berechtigung, Oberflächen-Profil

Siehe auch: [Anlagensicht und Wohnungssicht](#oberflaechen), [API-Token](#api-token)

## H

<a id="heizzeit"></a>
### Heizzeit

*Betrieb* — Wie lange eine Zone an einem Tag eine Heizanforderung hatte.

Die Zahl zeigt Entscheidungen der Regelung, keine bestätigte körperliche Heizwirkung. Sie ist weder Energie- noch Kostenmesser: Wie warm ein Raum wird, hängt zusätzlich von Außentemperatur, Gebäude und Lüften ab. Im Trockenlauf zeigt sie, wann geheizt worden wäre. Im Notbetrieb zeigt sie nicht den Takt der Aktoren. Stand der Dienst still, zählen die Lücken nur begrenzt mit.

Auch: Heizstatistik

Siehe auch: [Regelentscheidung](#regelentscheidung), [Trockenlauf](#trockenlauf), [Relaisverschleiß](#relaisverschleiss)

<a id="home-assistant"></a>
### Home Assistant

*Oberfläche* — Eine Smart-Home-Zentrale, die sich optional per MQTT mit thermoctl verbinden lässt.

Die Anbindung ist freiwillig. Über MQTT meldet thermoctl seine Zonen an und nimmt Sollwerte und Betriebsarten entgegen; das geht auch im Trockenlauf, dann wird aber nichts geschaltet. Die Entität „Regelung scharf“ zeigt nur die gespeicherte Freigabe; ob nach einem Neustart wirklich gesendet wird, zeigt die Betriebsseite.

Auch: HA, Home-Assistant-Add-on

Siehe auch: [MQTT und Broker](#mqtt-broker), [Homebridge](#homebridge), [Scharfschalten](#scharfschalten)

<a id="homebridge"></a>
### Homebridge

*Oberfläche* — Eine Brücke, die Zonen von thermoctl in Apple Home sichtbar macht.

Die Oberfläche erzeugt je Zone eine fertige Konfiguration für das Homebridge-Plugin mit den echten Topics der Anlage. Benutzername und Passwort darin sind Platzhalter: Homebridge braucht einen eigenen Broker-Zugang mit möglichst engen Rechten, nie den von thermoctl.

Auch: Apple Home, HomeKit

Siehe auch: [MQTT und Broker](#mqtt-broker), [Home Assistant](#home-assistant)

<a id="hysterese"></a>
### Hysterese

*Regelung* — Ein Spielraum um den Sollwert, damit die Heizung nicht ständig an- und ausgeht.

Die Regelung fordert Heizen erst an, wenn die Temperatur mehr als die Hysterese unter dem Sollwert liegt, und beendet es erst, wenn sie mehr als die Hysterese darüber liegt. Dazwischen bleibt der Zustand, wie er ist. Der Wert gilt anlagenweit (ab Werk 0,3 K) und lässt sich je Zone abweichend einstellen.

Auch: Hysterese (K), Schalthysterese, Hysterese-Regelung

Siehe auch: [Mindestschaltdauer](#mindestschaltdauer), [PI-Regelung (Beta)](#pi-regelung), [Sollwert](#sollwert)

## K

<a id="kiosk"></a>
### Kiosk

*Oberfläche* — Eine Anzeige für ein Wandtablet, die ohne Benutzerkonto über ein eigenes Token geöffnet wird.

Ein Kiosk-Token hat einen Namen, eine Auswahl sichtbarer Zonen und optional die Erlaubnis zu bedienen; sonst zeigt es nur an. Optional läuft es nach einigen Tagen ab, und es lässt sich jederzeit widerrufen. Die Adresse mit dem Token erscheint genau einmal im Klartext und gehört als Lesezeichen auf das Tablet.

Auch: Kiosk-Token, Wandtablet, Wandpanel, Tafel

Siehe auch: [API-Token](#api-token), [Anlagensicht und Wohnungssicht](#oberflaechen)

## M

<a id="meldungen"></a>
### Meldungen

*Betrieb* — Hinweise, die thermoctl bei Störungen an ein Ziel wie einen Webhook schickt.

Es gibt sechs Arten, jede einzeln abschaltbar: Sensorstörung, Brücke oder Broker weg, gescheiterter Schaltbefehl, festhängender Messwert, Fenster vergessen offen und Problemmeldung aus einer Wohnung. Bei Störungen werden Beginn und Entwarnung je einmal gemeldet; die Problemmeldung aus einer Wohnung ist eine einzelne Nachricht ohne Entwarnung. Wohin eine Meldung geht, steht unter Schnittstellen.

Auch: Störungsmeldungen, Benachrichtigungen, Webhook, Testmeldung

Siehe auch: [Festhängender Messwert](#festhaengender-messwert), [Fenster-Alarm](#fenster-alarm), [MQTT und Broker](#mqtt-broker)

<a id="meross"></a>
### Meross

*Sensorik* — Eine Herstellerreihe von WLAN-Steckdosen, die thermoctl über deren Cloud schaltet.

Meross-Steckdosen melden sich nicht über MQTT, sondern werden stündlich mit der Cloud abgeglichen. Zum Schalten braucht thermoctl eine Anmeldung dort. Ist sie gerade nicht möglich, wird nicht geschaltet: Der Befehl gilt als gescheitert und wird im nächsten Regelzyklus erneut versucht, die Anmeldung selbst nach einer Ablehnung dagegen mit wachsenden Pausen.

Auch: Meross-Steckdose, Steckdose

Siehe auch: [Aktor](#aktor), [Zigbee2MQTT und Brücke](#zigbee2mqtt)

<a id="messquelle"></a>
### Messquelle

*Sensorik* — Der Temperatursensor, nach dem eine Zone regelt.

Die Messquelle (auch Wandfühler oder Raumfühler) wird bei den Geräten der Zone aus den bekannten Geräten mit Temperaturmessung gewählt. Liefert sie nicht mehr, greift bei aktivem Notbetrieb der Zone eine Ersatzquelle oder der Notbetrieb; mehr dazu unter Sensor-Timeout.

Auch: Wandfühler, Raumfühler, Fühler, Temperatursensor, Messquelle wählen

Siehe auch: [Ersatzquelle](#ersatzquelle), [Sensor-Timeout](#sensor-timeout), [Sensorkalibrierung](#sensorkalibrierung)

<a id="mindestschaltdauer"></a>
### Mindestschaltdauer

*Regelung* — Wie lange ein Zustand (Heizen oder Aus) mindestens bestehen bleibt, bevor er wechseln darf.

Sie schützt Ventile und Relais vor ständigem Ein- und Ausschalten und gilt getrennt als Mindest-Einschaltdauer und Mindest-Ausschaltdauer. Solange die Zeit nicht um ist, hält die Regelung die Heizanforderung in der Regel unverändert, auch wenn die Temperatur einen Wechsel erlauben würde. Ausnahmen: Ein offenes Fenster (außer in Zonen, die es nicht abschaltet) und eine fehlende Messung beenden die Heizanforderung sofort, und ein Ventilschutzlauf kann die Dauer umgehen. Im Notbetrieb gilt der feste Takt, der die Mindestdauern nicht unterschreitet. Ab Werk sind es je 5 Minuten, anlagenweit einstellbar und je Zone abweichend.

Auch: Mindest-Einschaltdauer, Mindest-Ausschaltdauer, Mindestdauer, Taktschutz

Siehe auch: [Hysterese](#hysterese), [PI-Regelung (Beta)](#pi-regelung), [Relaisverschleiß](#relaisverschleiss)

<a id="mqtt-broker"></a>
### MQTT und Broker

*Sensorik* — MQTT ist ein Nachrichtenweg zwischen Geräten und Diensten; der Broker ist dessen Vermittlungsstelle.

Sensoren, Zigbee2MQTT, Home Assistant, Homebridge und thermoctl tauschen sich über einen MQTT-Broker aus. Fällt der Broker oder die Verbindung zur Brücke aus, kommen keine Messwerte mehr an. Dafür gibt es eine eigene Meldung. Adresse und Zugangsdaten werden in der Umgebung des Dienstes gesetzt, nicht in der Oberfläche.

Auch: MQTT, Broker, MQTT-Broker

Siehe auch: [Zigbee2MQTT und Brücke](#zigbee2mqtt), [Home Assistant](#home-assistant), [Homebridge](#homebridge), [Meldungen](#meldungen)

## N

<a id="notbetrieb"></a>
### Notbetrieb

*Sensorik* — Ersatzverhalten, wenn für eine Zone keine brauchbare Temperaturmessung mehr vorliegt.

Der Ablauf kennt vier Stufen: Normal, Ersatzquelle, Notbetrieb und Rückkehrprüfung. Er durchläuft nicht immer alle: Gibt es keine brauchbare Ersatzquelle, wechselt eine Zone direkt von Normal in den Notbetrieb. Im Notbetrieb takten Aktoren mit Ein/Aus-Schaltfunktion (etwa Fußbodenkreise) nach der Uhr (Festtakt oder Außenkennlinie), unabhängig von der Raumtemperatur. Ein Thermostat wird nur dann einmal je Störung auf den Notsollwert gestellt und danach nicht mehr angesprochen, wenn für das Gerät bestätigt ist, dass es Betriebsart und Zieltemperatur annimmt; sonst wird es gar nicht angesprochen. Im Trockenlauf wird das alles nur protokolliert. Ab Werk ist er für jede Zone aktiv und je Zone abschaltbar. Eine Notlösung, keine Regelung.

Auch: Notbetrieb bei Sensorausfall, Sensorausfall, Notbetriebsprofil

Siehe auch: [Ersatzquelle](#ersatzquelle), [Festtakt](#festtakt), [Außenkennlinie](#aussenkennlinie), [Notsollwert](#notsollwert), [Rückkehrprüfung](#rueckkehrpruefung)

<a id="notsollwert"></a>
### Notsollwert

*Sensorik* — Die Temperatur, auf die ein Thermostat im Notbetrieb einmalig gestellt wird.

Gilt anlagenweit (ab Werk 20 °C) und lässt sich je Zone überschreiben; ein leeres Feld erbt den Anlagenwert. Das Thermostat bekommt ihn zusammen mit der Betriebsart „manuell“ einmal je Störung, wenn für das Gerät bestätigt ist, dass es beides annimmt, und hält ihn dann selbst. Nach der Rückkehr wird die zuvor gemeldete Betriebsart zurückgeschrieben, falls sie bekannt war, und es gilt wieder der gewöhnliche Sollwert. Der Notsollwert gilt nur für Thermostate, nicht für Aktoren mit Ein/Aus-Schalter wie Fußbodenkreise.

Auch: Anlagenweiter Notsollwert, Eigener Notsollwert

Siehe auch: [Notbetrieb](#notbetrieb), [Selbstregelndes Thermostatventil](#selbstregelndes-thermostatventil)

## P

<a id="passkey"></a>
### Passkey

*Oberfläche* — Eine Anmeldung ohne Passwort, bei der ein geheimer Schlüssel das Gerät nie verlässt.

Ein Passkey gilt nur für diese Seite und lässt sich daher nicht auf einer nachgemachten Seite eingeben. Er braucht einen Browser mit Passkey-Unterstützung und eine verschlüsselte Verbindung. Solange kein Passkey hinterlegt ist, bleibt das Passwort der einzige Weg zur Anmeldung.

Auch: Passkeys, WebAuthn

Siehe auch: [Gruppen und Rechte](#gruppen-und-rechte), [API-Token](#api-token)

<a id="pi-regelung"></a>
### PI-Regelung (Beta)

*Regelung* — Eine optionale Regelart, die den Raum gleichmäßiger halten soll, dafür aber öfter schaltet.

Statt zwischen zwei Schwellen umzuschalten, berechnet der PI-Regler aus der Abweichung einen Tastgrad: den Anteil der Zeit, in der geheizt wird. Er wird für jeweils 15 Minuten festgelegt. PI schaltet deutlich öfter als die Hysterese und belastet Relais stärker. Sie ist je Zone abschaltbar (ab Werk aus) und nur mit gewöhnlichem Schaltaktor möglich; bei Einschränkungen regelt die Zone über die Hysterese.

Auch: PI, PI-Regler, Tastgrad, Verstärkung Kp, Nachstellzeit Ti, Beta

Siehe auch: [Hysterese](#hysterese), [Relaisverschleiß](#relaisverschleiss), [Mindestschaltdauer](#mindestschaltdauer)

## R

<a id="regelentscheidung"></a>
### Regelentscheidung

*Regelung* — Das Ergebnis eines Regelzyklus für eine Zone: Heizen oder nicht, mit Begründung.

Zu jeder Entscheidung nennt thermoctl, warum so entschieden wurde, zum Beispiel, dass die Temperatur unter dem Sollwert minus Hysterese liegt, dass ein Fenster offen ist oder dass die Mindestdauer des aktuellen Zustands noch nicht erreicht ist. Im Trockenlauf wird sie nur protokolliert, scharf geht sie an die Aktoren; im Notbetrieb treten dort Takt bzw. Übergabe an ihre Stelle. Die Oberfläche spricht von Heizanforderung und, im Trockenlauf, von Schattenentscheidung.

Auch: Entscheidung, Begründung, Schattenentscheidung, Heizanforderung, Würde heizen

Siehe auch: [Regelzyklus](#regelzyklus), [Hysterese](#hysterese), [Trockenlauf](#trockenlauf), [Schattenbetrieb](#schattenbetrieb)

<a id="regelzyklus"></a>
### Regelzyklus

*Regelung* — Der Takt, in dem die Regelung für jede Zone neu entscheidet.

In jedem Durchlauf wird für jede Zone der Sollwert ermittelt und entschieden, ob geheizt werden soll. Die Dauer ist anlagenweit einstellbar, ab Werk 60 Sekunden. Sie ist auch die Zeiteinheit des Aktiv-Bereitschafts-Verbunds.

Auch: Regelzyklus (Sekunden), Zyklus, Regelintervall

Siehe auch: [Regelentscheidung](#regelentscheidung), [Aktiv-Bereitschafts-Verbund](#aktiv-bereitschafts-verbund)

<a id="relaisverschleiss"></a>
### Relaisverschleiß

*Betrieb* — Zählt, wie oft Relais schalten, und rechnet das auf ein Jahr hoch.

Ein Schaltspiel ist jeder bestätigt gesendete Wechsel von Aus nach Ein oder von Ein nach Aus; gezählt wird, was thermoctl befohlen hat, nicht, was das Relais getan hat. Der erste bekannte Befehl ist nur der Ausgangspunkt. Gezählt wird gegen eine angenommene Lebensdauer (ab Werk 500.000 Schaltspiele): ein austauschbarer Vergleichswert, keine Herstellerangabe. Befehle im Trockenlauf, gescheiterte Befehle und Sollwerte an Thermostatventile zählen nicht.

Auch: Schaltspiel, Schaltspiele, Relais-Lebensdauer, Jahreshochrechnung

Siehe auch: [PI-Regelung (Beta)](#pi-regelung), [Hysterese](#hysterese), [Mindestschaltdauer](#mindestschaltdauer), [Schaltprotokoll](#schaltprotokoll)

<a id="rueckkehrpruefung"></a>
### Rückkehrprüfung

*Sensorik* — Die Probezeit, nach der eine Zone nach einem Ausfall wieder einer Quelle vertraut: dem Wandfühler oder der Ersatzquelle.

Geprüft wird immer genau eine Quelle: der Wandfühler, wenn er wieder liefert, sonst die Ersatzquelle. Sie muss mehrere neue Messwerte liefern (ab Werk zwei) und dabei durchgehend brauchbar bleiben (ab Werk 60 Sekunden); beides wird gleichzeitig geprüft und lässt sich über das Notbetriebsprofil einstellen. Fällt die Quelle währenddessen wieder aus, beginnt die Prüfung von vorn; aus der Rückkehrprüfung selbst führt das zurück in den Notbetrieb. Besteht der Wandfühler, übernimmt die normale Regelung; besteht nur die Ersatzquelle, regelt die Zone mit ihr weiter. Thermostate bekommen danach ihre frühere Betriebsart einmal zurück, falls sie bekannt war.

Auch: Rückkehr, Rückkehr: Mindestdauer stabil, Rückkehr: nötige Messungen

Siehe auch: [Notbetrieb](#notbetrieb), [Ersatzquelle](#ersatzquelle), [Messquelle](#messquelle)

## S

<a id="schaltprotokoll"></a>
### Schaltprotokoll

*Betrieb* — Das Journal der Befehle an Geräte, auch derer, die im Trockenlauf unterdrückt wurden.

Jeder Eintrag nennt Zeitpunkt, Zone, Gerät, Befehl, Ergebnis (ausgeführt, unterdrückt oder gescheitert) und eine Begründung. Ein gescheiterter Befehl, der sich unverändert wiederholt, wird nur beim Wechsel des Ergebnisses neu eingetragen. Dazu kommen Entscheidungen des Notbetriebs, eindeutig gekennzeichnet. Die Befehle löscht der Dienst nicht selbst, weil ein einzelner Eintrag noch nach Wochen eine Störung erklären kann. Die Notbetriebsentscheidungen dagegen werden nach der eingestellten Frist für Schattenentscheidungen gelöscht.

Auch: Befehlsprotokoll, Befehle, Schaltbefehl

Siehe auch: [Trockenlauf](#trockenlauf), [Notbetrieb](#notbetrieb), [Relaisverschleiß](#relaisverschleiss)

<a id="scharfschalten"></a>
### Scharfschalten

*Betrieb* — Gibt frei, dass die Regelung wirklich Befehle an Aktoren sendet.

Dafür gibt es zwei Riegel. Der erste ist die gespeicherte Freigabe auf der Betriebsseite (mit Begründung, jederzeit zurücknehmbar). Der zweite wird beim Start des Dienstes aus dieser Freigabe gebildet und danach nicht mehr verändert; er gilt für Befehle über MQTT (Zigbee2MQTT) ebenso wie für Meross. Wurde der Dienst im Trockenlauf gestartet, braucht es darum nach dem Scharfschalten einen Neustart; bis dahin zeigt die Oberfläche „Scharf, Neustart fehlt“. Im Aktiv-Bereitschafts-Verbund sendet außerdem nur die führende Instanz. Das Zurücknehmen wirkt sofort; bereits gesendete Befehle bleiben wirksam.

Auch: scharf, Scharf schalten, Riegel, MQTT-Riegel, Freigabe, Neustart fehlt

Siehe auch: [Trockenlauf](#trockenlauf), [Schattenbetrieb](#schattenbetrieb), [Aktor](#aktor)

<a id="schattenbetrieb"></a>
### Schattenbetrieb

*Betrieb* — Die Anlaufphase, in der thermoctl mitentscheidet, ohne zu schalten, und gegen die bisherige Steuerung verglichen wird.

Technisch derselbe Zustand wie der Trockenlauf: Die Regelung liest, entscheidet und protokolliert, aber keine Befehle erreichen einen Aktor. Die Entscheidungen bleiben als Schattenentscheidungen erhalten (einstellbar, ab Werk 365 Tage; dieselbe Frist gilt für die Entscheidungen des Notbetriebs), damit man sie über Tage mit der bisherigen Steuerung vergleichen kann, bevor man scharf schaltet.

Auch: Schattenprotokoll, Schattenentscheidungen, Schattenlauf

Siehe auch: [Trockenlauf](#trockenlauf), [Scharfschalten](#scharfschalten), [Regelentscheidung](#regelentscheidung)

<a id="selbstregelndes-thermostatventil"></a>
### Selbstregelndes Thermostatventil

*Betrieb* — Ein Heizkörperventil, das mit einer vorgegebenen Zieltemperatur selbst regelt.

thermoctl schreibt nur die Zieltemperatur und, wenn das Gerät es annimmt, die Raumtemperatur; das Ventil regelt damit selbst. Bei einem nicht selbstregelnden Ventil entscheidet dagegen thermoctl über Ein und Aus, mit Hysterese und Mindestschaltdauer; ein offenes Fenster schaltet eine solche Zone nicht ab (siehe Fensterkontakt). Im Notbetrieb zählt nicht diese Einstellung, sondern, welche Fähigkeiten das Gerät hat (siehe Notbetrieb).

Auch: selbstregelnd, Thermostatventil, Thermostat, Heizkörperthermostat

Siehe auch: [Aktor](#aktor), [Notsollwert](#notsollwert), [Echo-Regel](#echo-regel), [Hysterese](#hysterese)

<a id="sensor-timeout"></a>
### Sensor-Timeout

*Sensorik* — Die Zeit ohne neuen Messwert, nach der ein Sensor als ausgefallen („veraltet“) gilt.

Ab Werk 30 Minuten, anlagenweit einstellbar und je Zone abweichend. Danach traut die Zone dem letzten Messwert nicht mehr. Je nach Konfiguration greifen Ersatzquelle und Notbetrieb; ohne sie regelt die Zone gegen den Frostschutz-Sollwert. Gibt es gar keine Messung, bleibt die Heizanforderung aus.

Auch: Sensor gilt als ausgefallen nach, veraltet, Messwert veraltet, keine Quelle

Siehe auch: [Messquelle](#messquelle), [Ersatzquelle](#ersatzquelle), [Notbetrieb](#notbetrieb), [Frostschutz](#frostschutz), [Festhängender Messwert](#festhaengender-messwert)

<a id="sensorkalibrierung"></a>
### Sensorkalibrierung

*Sensorik* — Ein fester Betrag in Kelvin, der auf die Messung eines Sensors aufgerechnet wird.

Der Wert wird zur gemessenen Temperatur addiert, um eine bekannte Abweichung des Sensors auszugleichen. Negative Werte sind erlaubt. Er gilt je Zone und beeinflusst alle Regelentscheidungen. Nicht zu verwechseln mit dem Ausgleichswert eines Thermostats, der von dessen Messung abgezogen wird.

Auch: Temperaturkorrektur, Temperaturversatz, Offset, Kalibrierung

Siehe auch: [Ausgleichswert](#ausgleichswert), [Messquelle](#messquelle)

<a id="sollwert"></a>
### Sollwert

*Regelung* — Die Temperatur, die ein Raum gerade erreichen soll.

Er ergibt sich in dieser Reihenfolge: Betriebsart „Aus“ (dann Frostschutz), laufende Übersteuerung, Urlaub, Zeitplan und zuletzt Frostschutz. Die Oberfläche nennt zu jedem Sollwert die Begründung, woher er stammt. Die Sonnenabsenkung kann ihn zusätzlich senken.

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

*Regelung* — Senkt den Sollwert ab, wenn bald Sonne zu erwarten ist, die den Raum ohnehin erwärmt.

Meldet die Wettervorhersage in den nächsten Stunden (einstellbar, ab Werk 3) eine Einstrahlung von mindestens 120 W/m², sinkt der Sollwert einer Zone um bis zu ihre Obergrenze (ab Werk 2 K), gewichtet mit dem Sonnenprofil der Zone von 0 bis 1. Der Frostschutz-Sollwert bleibt die Untergrenze. Ohne Standort bleibt die Funktion aus, ebenso bei nicht erreichbarer Vorhersage.

Auch: Sonnenprofil, Sonnenabsenkung, Obergrenze, Solarabsenkung, Sonnenvorhersage

Siehe auch: [Frostschutz](#frostschutz), [Sollwert](#sollwert)

## T

<a id="trockenlauf"></a>
### Trockenlauf

*Betrieb* — Die Regelung entscheidet und protokolliert, schaltet aber nichts.

Das ist der Zustand nach jeder Einrichtung. Entscheidungen und Begründungen werden aufgezeichnet, aber weder Sollwerte noch Ein/Aus-Befehle gehen an Aktoren. So lassen sich Entscheidungen über mehrere Tage beobachten, bevor sie etwas schalten. Das Scharfschalten lässt sich jederzeit zurücknehmen; bereits gesendete Befehle bleiben wirksam.

Auch: unscharf, Trockenlauf-Modus, im Trockenlauf unterdrückt

Siehe auch: [Scharfschalten](#scharfschalten), [Schattenbetrieb](#schattenbetrieb), [Schaltprotokoll](#schaltprotokoll)

## U

<a id="uebersteuerung"></a>
### Übersteuerung

*Regelung* — Eine vorübergehend von Hand gesetzte Temperatur, die den Zeitplan überlagert.

Sie gilt bis zu einem gewählten Ende oder bis auf Widerruf; danach gilt wieder, was sonst gälte: der Urlaub, sonst der Zeitplan, der unverändert bleibt. Nur die Betriebsart „Aus“ steht darüber. Ein Boost („Für eine Weile wärmer“) ist eine Übersteuerung, ebenso die Abwesenheit je Raum.

Auch: Boost, Für eine Weile wärmer, Übersteuern, Override

Siehe auch: [Sollwert](#sollwert), [Zeitplan](#zeitplan), [Abwesenheit](#abwesenheit), [Betriebsart](#betriebsart)

<a id="urlaub"></a>
### Urlaub

*Regelung* — Senkt jede Zone der Anlage über einen Zeitraum auf einen einzigen Absenkwert.

Die Verwaltung setzt ersten und letzten Tag sowie die Absenktemperatur für die ganze Anlage. Liegt sie unter dem Frostschutz einer Zone, gilt für diese der Frostschutz. Fenster, Sensorausfall, Mindestschaltdauer und Ventilschutz gelten weiter, eine Zone in Betriebsart „Aus“ bleibt aus, und eine von Hand gesetzte Übersteuerung bleibt bestehen. Zu unterscheiden von der Abwesenheit einzelner Bewohner.

Auch: Urlaubsbetrieb, Absenkwert, Urlaub geplant, Urlaub läuft

Siehe auch: [Abwesenheit](#abwesenheit), [Übersteuerung](#uebersteuerung), [Sollwert](#sollwert)

## V

<a id="ventilschutz"></a>
### Ventilschutz

*Regelung* — Ein regelmäßiger kurzer Heizlauf, damit Ventile nach langem Stillstand nicht festsitzen.

Hat eine Zone länger als den eingestellten Abstand (Tage) nicht regulär geheizt, fordert die Regelung einmal für die eingestellte Dauer (Minuten) Heizen an. Er ist je Zone abschaltbar (ab Werk aus) und läuft nur bei gutem Sensor, ohne Übersteuerung und nicht bei Betriebsart „Aus“. Er hat den niedrigsten Rang: Wo die Raumregelung ohnehin heizt oder ein offenes Fenster abschaltet, entscheidet diese. Im Trockenlauf wird die Entscheidung nur protokolliert.

Auch: Ventilschutzlauf, Ventilschutz-Abstand, Ventilschutz-Dauer, Schutzlauf

Siehe auch: [Mindestschaltdauer](#mindestschaltdauer), [Trockenlauf](#trockenlauf), [Aktor](#aktor)

## W

<a id="wiederanlaufspanne"></a>
### Wiederanlaufspanne

*Sensorik* — Verhindert, dass der Notbetrieb am warmen Ende der Außenkennlinie ständig neu anläuft.

Sobald der warme Aus-Punkt der Kennlinie erreicht ist, bleibt Ein bei 0 Sekunden, bis die Außentemperatur um diese Spanne darunter fällt (ab Werk 1 K). So flattert ein Wert um den Aus-Punkt nicht zwischen Heizen und Aus.

Auch: Warm-Aus-Hysterese, Wiederanlaufspanne (K)

Siehe auch: [Außenkennlinie](#aussenkennlinie), [Notbetrieb](#notbetrieb), [Hysterese](#hysterese)

## Z

<a id="zeitplan"></a>
### Zeitplan

*Regelung* — Der Wochenplan einer Zone: Schaltpunkte, die zu bestimmten Zeiten den Sollwert-Modus wechseln.

Ein Schaltpunkt gilt bis zum nächsten, Woche für Woche. Übersteuerung, Urlaub und Betriebsart „Aus“ gehen vor. Solange kein Schaltpunkt eingetragen ist, gilt der Frostschutz. Die Vorschau zeigt auch eine gerade laufende Übersteuerung, nicht nur den gemalten Plan.

Auch: Wochenplan, Schaltpunkt, Schaltpunkte, Schaltzeit, Heizzeit-Plan

Siehe auch: [Sollwert-Modus](#sollwert-modus), [Sollwert](#sollwert), [Übersteuerung](#uebersteuerung), [Frostschutz](#frostschutz)

<a id="zigbee2mqtt"></a>
### Zigbee2MQTT und Brücke

*Sensorik* — Die Software, die Zigbee-Funkgeräte über MQTT zugänglich macht; die Brücke ist ihre Verbindung.

Funksensoren, Thermostate und Schalter hängen an der Zigbee2MQTT-Brücke. Meldet sie sich nicht, kommt von keinem einzigen Gerät etwas an, unabhängig von der Zuordnung zu Zonen. Neue Geräte erscheinen von selbst in der Geräteliste und werden von Hand einer Zone zugeordnet.

Auch: Zigbee, Zigbee-Brücke, Brücke, Z2M

Siehe auch: [MQTT und Broker](#mqtt-broker), [Messquelle](#messquelle), [Aktor](#aktor)

<a id="zone"></a>
### Zone

*Betrieb* — Ein Raum oder eine Gruppe von Räumen mit eigenem Sollwert.

Einer Zone sind eine Messquelle, Aktoren, ein Zeitplan, Sollwerte und Regelparameter zugeordnet. Ohne mindestens eine Zone gibt es nichts zu regeln. Was die Zone nicht selbst einstellt, erbt sie aus den anlagenweiten Regelvorgaben.

Auch: Raum, Zonen

Siehe auch: [Messquelle](#messquelle), [Aktor](#aktor), [Zeitplan](#zeitplan), [Anlagensicht und Wohnungssicht](#oberflaechen)
