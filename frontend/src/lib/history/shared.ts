// Gemeinsame Begrenzung der Abrufe der Auswertung: Auf einer Seite mit vielen Karten läuft
// immer nur ein Abruf. Jede Reihe kann bis zu 720 Fächer aus Zehntausenden Zeilen verdichten, und
// bei SQLite scheitern gleichzeitige Anfragen am Schreibsperre der Sitzungsauffrischung
// („database is locked“, 500) -- nacheinander geladen kommt jede durch.
import { createLimiter } from './limiter';

export const historyLimiter = createLimiter(1);
