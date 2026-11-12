/** Wartezeit, nach der die Anmeldeseite erklärt, warum die Prüfung dauert. */
export const SLOW_LOGIN_HINT_DELAY_MS = 1000;

/**
 * Der Server drosselt die Anmeldung absichtlich: Er wartet je nach bisherigen Fehlversuchen
 * 1 bis 5 s, bevor er antwortet (`throttle` in `thermoctl/web/shared/login.py`).
 */
export const SLOW_LOGIN_HINT =
  'Die Prüfung dauert einen Moment – aus Sicherheitsgründen antwortet der Server bei Anmeldungen verzögert.';

/**
 * Setzt nach `delayMs` die Meldung; der zurückgegebene Abbruch nimmt sie zurück und
 * verhindert, dass sie noch erscheint. Er ist mehrfach aufrufbar.
 */
export function scheduleSlowHint(
  show: (message: string | null) => void,
  delayMs: number = SLOW_LOGIN_HINT_DELAY_MS,
): () => void {
  const timer = setTimeout(() => show(SLOW_LOGIN_HINT), delayMs);
  return () => {
    clearTimeout(timer);
    show(null);
  };
}
