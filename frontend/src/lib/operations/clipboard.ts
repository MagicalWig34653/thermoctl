// In die Zwischenablage kopieren, mit Rückfall. `navigator.clipboard` gibt es nur in einem
// „sicheren Kontext“ (HTTPS oder localhost); thermoctl läuft im Heimnetz oft über reines HTTP,
// dort ist die Schnittstelle schlicht nicht vorhanden. Ein Knopf, der dann stumm nichts tut,
// wäre schlimmer als keiner. Der Rückfall (`execCommand('copy')` auf einem versteckten Feld)
// funktioniert dort noch; scheitert auch er, sagt der Knopf es ehrlich.

export interface ClipboardEnv {
  isSecureContext: boolean;
  writeText: ((text: string) => Promise<void>) | null;
  /** Kopiert über Auswahl eines versteckten Feldes; `false`, wenn der Browser es verweigert. */
  selectAndCopy: (text: string) => boolean;
}

export type CopyResult = { ok: true; text: string } | { ok: false; text: string };

export const COPY_OK = 'In die Zwischenablage kopiert.';
export const COPY_FAILED = 'Kopieren nicht möglich. Text bitte markieren und selbst kopieren.';

export async function copyText(text: string, env: ClipboardEnv): Promise<CopyResult> {
  if (env.isSecureContext && env.writeText) {
    try {
      await env.writeText(text);
      return { ok: true, text: COPY_OK };
    } catch {
      // abgelehnt (Berechtigung, Fokus): derselbe Rückfall wie ohne Schnittstelle
    }
  }
  let copied = false;
  try {
    copied = env.selectAndCopy(text);
  } catch {
    copied = false;
  }
  return copied ? { ok: true, text: COPY_OK } : { ok: false, text: COPY_FAILED };
}

/** Die Umgebung des echten Browsers. */
export function browserClipboardEnv(): ClipboardEnv {
  const clip = typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
  return {
    isSecureContext: typeof window !== 'undefined' && window.isSecureContext,
    writeText: clip && clip.writeText ? (t) => clip.writeText(t) : null,
    selectAndCopy(text) {
      const field = document.createElement('textarea');
      field.value = text;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.top = '-1000px';
      field.style.left = '-1000px';
      document.body.appendChild(field);
      field.focus();
      field.select();
      try {
        return document.execCommand('copy');
      } finally {
        document.body.removeChild(field);
      }
    },
  };
}
