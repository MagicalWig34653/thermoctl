// Der Erklärtext „Was bedeuten die Anzeigen?“ der alten Hilfeseite (`/account/help`,
// templates/help.html). Reiner Text ohne Anlagendaten: Die Seite braucht kein Recht und kann
// nichts über fremde Räume verraten.

export interface Explanation {
  id: string;
  title: string;
  text: string;
}

export const EXPLANATIONS: readonly Explanation[] = [
  {
    id: 'sollwert',
    title: 'Sollwert',
    text: 'Die Temperatur, die dein Raum gerade erreichen soll. Sie kommt aus dem Zeitplan – oder, falls du gerade etwas geändert hast, aus deiner Änderung. Darunter steht immer, woher der Wert stammt.',
  },
  {
    id: 'heizt-wartet',
    title: 'Heizt / wartet',
    text: '„Heizt“ heißt: dein Raum ist kühler als der Sollwert und wird gerade erwärmt. „Wartet“ heißt: der Sollwert ist erreicht. Beides wechselt im Tagesverlauf mehrfach; das ist normal und kein Fehler.',
  },
  {
    id: 'zeitplan',
    title: 'Zeitplan',
    text: 'Der Wochenplan legt fest, ab wann es morgens warm und ab wann es abends kühler sein soll. Er gilt dauerhaft, Woche für Woche.',
  },
  {
    id: 'normal-aendern',
    title: 'Normale Temperatur ändern',
    text: 'Die Tasten − und + ändern die Temperatur des gerade laufenden Abschnitts dauerhaft. Wer also abends auf 21,5 °C stellt, hat ab jetzt jeden Abend 21,5 °C.',
  },
  {
    id: 'zeitweise-waermer',
    title: 'Für eine Weile wärmer',
    text: 'Gilt nur vorübergehend und endet von selbst. Danach zählt wieder der Zeitplan – der Wochenplan bleibt dabei unverändert.',
  },
  {
    id: 'messwert-alt',
    title: 'Messwert möglicherweise nicht aktuell',
    text: 'Der Temperaturfühler hat sich länger nicht gemeldet. Die Heizung regelt weiter, aber die angezeigte Zahl kann älter sein als sie aussieht. Hält der Hinweis über Stunden an, ist meist die Batterie des Fühlers leer.',
  },
];

/** Hinweis am Ende der alten Seite: wohin man bei einem unbekannten Wort geht. */
export const GLOSSARY_HINT =
  'Ein Wort verstehst du nicht? Weiter unten stehen alle Fachbegriffe in einfachen Worten, von A bis Z und mit Suchfeld.';
