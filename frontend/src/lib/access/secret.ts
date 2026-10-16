// Lebenslauf eines einmalig gezeigten Klartexts (API-Token, Kiosk-Token).
//
// Der Klartext steht nur in diesem Halter, und der Halter lebt nur, solange der Dialog offen
// ist. Er landet nie in Listenzustand, localStorage/sessionStorage, Adresse, Konsole oder
// Fehlermeldungen. `clear()` überschreibt den Verweis; danach ist er nicht mehr lesbar.
// (Im Speicher des Browsers lässt sich ein JavaScript-String nicht gezielt überschreiben --
// wir geben nur jeden Verweis frei.)

export interface SecretHolder {
  /** Klartext, solange der Dialog offen ist, sonst null. */
  readonly value: string | null;
  /** Zusatzangabe (z. B. Name des Tokens), ohne Geheimnis. */
  readonly label: string | null;
  readonly open: boolean;
  show(plaintext: string, label: string): void;
  clear(): void;
}

export function createSecretHolder(): SecretHolder {
  let secret: string | null = null;
  let label: string | null = null;
  return {
    get value() {
      return secret;
    },
    get label() {
      return label;
    },
    get open() {
      return secret !== null;
    },
    show(plaintext, name) {
      secret = plaintext;
      label = name;
    },
    clear() {
      secret = null;
      label = null;
    },
    // Ein versehentliches JSON.stringify/String des Halters gibt den Klartext nicht preis.
    toJSON() {
      return { open: secret !== null };
    },
    toString() {
      return '[SecretHolder]';
    },
  } as SecretHolder;
}

/** Adresse fürs Wandtablett aus dem Klartext. Das Ergebnis ist ebenfalls geheim -- nur im Dialog zeigen. */
export function kioskLink(origin: string, prefix: string, plaintext: string): string {
  return `${origin}${prefix}/kiosk/${plaintext}`;
}
