// Kurze Erklärungen für Begriffe, die ohne Vorwissen nicht selbsterklärend sind
// (Hilfe-Icons mit Popover). Bewusst knapp; das Glossar bleibt die ausführliche Quelle.

export interface Term {
  title: string;
  text: string;
}

export const TERMS: Record<string, Term> = {
  setpoint: {
    title: 'Sollwert',
    text: 'Die Temperatur, die der Raum gerade erreichen soll. Sie kommt aus dem Wochenplan, einer Übersteuerung, dem Urlaub oder dem Frostschutz.',
  },
  planned: {
    title: 'Geplanter Sollwert',
    text: 'Das, was der Wochenplan jetzt vorsieht. Weicht der wirksame Sollwert davon ab, greift gerade etwas anderes ein, etwa eine Übersteuerung.',
  },
  heat_request: {
    title: 'Heizanforderung',
    text: 'Das Ergebnis der letzten Regelentscheidung: Der Raum ist zu kalt und sollte geheizt werden. Es sagt nicht, dass ein Gerät tatsächlich geschaltet hat – dafür zählt die Freigabe der Anlage.',
  },
  override: {
    title: 'Übersteuerung',
    text: 'Eine befristete Ausnahme vom Wochenplan. Danach gilt wieder der Plan. Sie ändert den Plan nicht.',
  },
  step: {
    title: 'Sollwert des Modus',
    text: 'Der Stepper ändert den Sollwert des gerade aktiven Temperaturmodus um ein halbes Grad und gilt dauerhaft, nicht befristet.',
  },
  operating_mode: {
    title: 'Betriebsart',
    text: 'Bestimmt, ob der Raum nach Plan regelt oder ausgeschaltet ist. „Aus“ hält nur den Frostschutz.',
  },
  jump_next: {
    title: 'Nächste Phase vorziehen',
    text: 'Setzt die Temperatur der nächsten Plan-Phase sofort, bis diese Phase ohnehin beginnt.',
  },
  mode_setpoints: {
    title: 'Temperaturen je Modus',
    text: 'Jeder Temperaturmodus (zum Beispiel Tag oder Nacht) hat pro Raum eine eigene Temperatur. Leer lassen entfernt sie.',
  },
  sensor: {
    title: 'Sensor',
    text: 'Zustand der Temperaturmessung. Ist er gestört oder zu alt, greift der Notbetrieb statt der normalen Regelung.',
  },
  decision: {
    title: 'Regelentscheidung',
    text: 'Warum zuletzt geheizt oder nicht geheizt werden sollte – so, wie die Regelung es begründet.',
  },
  day_track: {
    title: 'Tagesverlauf',
    text: 'Die Phasen des Wochenplans für heute. Wärmere Phasen sind oranger, kältere bläulicher. Die Linie zeigt die aktuelle Uhrzeit in der Zeitzone der Anlage.',
  },
  control_armed: {
    title: 'Gespeicherte Freigabe',
    text: 'Ob die Anlage scharf gestellt ist. Ohne Freigabe läuft die Regelung im Trockenlauf: Sie entscheidet und protokolliert, schaltet aber nichts.',
  },
  sending_allowed: {
    title: 'MQTT-Sendefreigabe',
    text: 'Ob dieser Dienst beim Start Schaltbefehle über MQTT senden darf. Sie wird nur beim Start gesetzt – eine gespeicherte Freigabe wirkt deshalb erst nach einem Neustart.',
  },
  cluster_standby: {
    title: 'Führung',
    text: 'Bei mehreren Instanzen schaltet nur die führende. Eine Reserve entscheidet nicht und sendet nichts.',
  },
  emergency: {
    title: 'Notbetrieb',
    text: 'Ersatzregelung, wenn der Sensor ausfällt oder veraltet ist.',
  },
};
