// AGPL-3.0 §13: Wer die Anwendung über ein Netz benutzt, muss den Quelltext erreichen können.
// Eine Stelle für Adresse und Beschriftung; Shell, Hilfe und Kiosk benutzen dieselbe.
export const SOURCE_URL = 'https://github.com/MagicalWig34653/thermoctl';
export const SOURCE_LABEL = 'Quelltext (AGPL-3.0)';
// Der Verweis führt aus der Anwendung hinaus, darum in einem neuen Tab (wie am Alt-Kiosk).
export const SOURCE_LINK = { href: SOURCE_URL, target: '_blank', rel: 'noopener noreferrer' } as const;
