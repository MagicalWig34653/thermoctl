// Kurze Erklärungen für die Regelungs- und Betriebsseiten (Hilfe-Icons mit Popover).
import type { Term } from '../rooms/glossary';

export const OPS_TERMS: Record<string, Term> = {
  armed: {
    title: 'Gespeicherte Freigabe',
    text: 'Der in der Datenbank gespeicherte Schalter „Regelung freigegeben“. Er überdauert den Neustart, schaltet aber allein nichts: Dazu muss auch die Sendefreigabe (MQTT und Meross) stehen.',
  },
  sending: {
    title: 'Sendefreigabe (MQTT und Meross)',
    text: 'Ein Riegel, den der Prozess beim Start baut. Er gilt für alle Wege, auf denen Befehle hinausgehen: MQTT und Meross. Eine Freigabe im laufenden Betrieb öffnet ihn nicht; erst ein Neustart liest die gespeicherte Freigabe neu ein.',
  },
  role: {
    title: 'Führungsrolle im Verbund',
    text: 'Laufen mehrere Instanzen, führt genau eine. Die übrigen sind Reserve und schalten nicht, auch wenn Freigabe und Sendefreigabe stehen. Direkt nach einem Start heißt es „wird ermittelt“: Die Instanz wartet, bis der Anspruch einer eventuell abgestürzten Vorgängerin abläuft. Währenddessen schaltet sie nichts.',
  },
  dry_run: {
    title: 'Trockenlauf',
    text: 'Die Regelung entscheidet und protokolliert, sendet aber nichts an die Aktoren. So lässt sich prüfen, was sie täte, bevor sie wirklich schaltet.',
  },
  arm_reason: {
    title: 'Begründung',
    text: 'Beim Scharfschalten ist eine kurze Begründung Pflicht. Sie wird im Ereignisprotokoll festgehalten, damit später nachvollziehbar ist, worauf sich die Freigabe stützte.',
  },
  hysteresis: {
    title: 'Hysterese',
    text: 'Abstand um den Sollwert, in dem die Regelung nicht umschaltet. Ohne ihn würde sie am Sollwert in jedem Zyklus ein- und ausschalten.',
  },
  min_times: {
    title: 'Mindestzeiten',
    text: 'So lange bleibt ein Ausgang mindestens an beziehungsweise aus, bevor er wieder wechseln darf. Das schont Relais und Ventile.',
  },
  inherit: {
    title: 'Erben',
    text: 'Ein leeres Feld bedeutet: Der Raum übernimmt den Wert der Anlage. Ein eigener Wert gilt nur für diesen Raum.',
  },
  pi: {
    title: 'PI-Regler (Beta)',
    text: 'Ein Regler, der stetig nachregelt, statt nur ein- und auszuschalten. Er schaltet häufiger und braucht passende Parameter. Ob er für diesen Raum zulässig ist, sagt der Server.',
  },
  valve_protection: {
    title: 'Ventilschutz',
    text: 'Bewegt ein Ventil in Abständen kurz, wenn es lange nicht gebraucht wurde, damit es nicht festsitzt.',
  },
  window_alarm: {
    title: 'Fenster-Alarm',
    text: 'Meldet ein Fenster, das länger als eingestellt offen steht, solange es draußen kalt genug ist, dass das teuer oder riskant wird.',
  },
  temp_drop: {
    title: 'Erkennung per Temperatursturz',
    text: 'Erkennt ein geöffnetes Fenster auch ohne Fensterkontakt an einem steilen Temperaturabfall. Die Schwellen sind Richtwerte, keine Messung Ihrer Anlage.',
  },
  emergency_curve: {
    title: 'Außentemperaturkurve',
    text: 'Im Notbetrieb ohne Raumfühler taktet der Ausgang. Mit Kurvenpunkten hängt der Takt von der Außentemperatur ab; ohne Punkte gilt der Festtakt.',
  },
  recovery: {
    title: 'Rückkehrprüfung',
    text: 'Nach einem Sensorfehler wartet die Regelung diese Zeit und diese Anzahl guter Messwerte ab, bevor sie in den Normalbetrieb zurückkehrt.',
  },
  emergency_setpoint: {
    title: 'Notsollwert',
    text: 'Zieltemperatur, die im Notbetrieb gilt, solange kein Raumfühler verfügbar ist.',
  },
  solar: {
    title: 'Sonnenabsenkung',
    text: 'Senkt den Sollwert vorübergehend ab, wenn Sonnenwärme erwartet wird. Der Wochenplan bleibt unverändert.',
  },
  solar_max: {
    title: 'Maximale Absenkung',
    text: 'Um wie viel Kelvin (1 K entspricht 1 °C Unterschied) der Sollwert durch die Sonne höchstens sinken darf. Räume können eigene Werte haben.',
  },
  solar_lookahead: {
    title: 'Vorausschau',
    text: 'Wie viele Stunden im Voraus die erwartete Sonnenwärme berücksichtigt wird.',
  },
  outdoor: {
    title: 'Außenfühler',
    text: 'Das Gerät, dessen Temperatur als Außentemperatur gilt. Sie fließt in Fenster-Alarm und Notbetrieb ein.',
  },
  webhook: {
    title: 'Webhook',
    text: 'Die Adresse, an die Meldungen gesendet werden. Sie steht in der Umgebung des Servers und wird hier nie angezeigt oder geändert.',
  },
  test_send: {
    title: 'Testmeldung',
    text: 'Sendet wirklich eine Meldung über den hinterlegten Webhook. Kurz nach einem Versuch ist ein neuer gesperrt.',
  },
};
