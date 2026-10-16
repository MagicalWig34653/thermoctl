// Reine Pfadlogik der Oberfläche (ohne Svelte, damit sie mit node:test prüfbar ist).
//
// Die Oberfläche liegt unter `{prefix}/app/...`. Der Präfix ist zur Build-Zeit unbekannt
// (Home-Assistant-Ingress) und kommt zur Laufzeit aus der Seite (`runtimePrefix`).

import { AREAS, areaPath, type VisibleArea } from './navigation';

export const APP_SEGMENT = '/app';

/** Pfad innerhalb der Oberfläche (`/zonen`), aus dem vollen Browserpfad abgeleitet.
 *  Ohne Grenzprüfung würde `/ing/application` als `/ing/app` + `lication` gelesen. */
export function relativePath(pathname: string, prefix: string): string {
  const base = `${prefix}${APP_SEGMENT}`;
  if (pathname !== base && !pathname.startsWith(`${base}/`)) return '/';
  return pathname.slice(base.length) || '/';
}

/** Die eigenständige Kioskansicht (`{prefix}/app/kiosk`) ist keine Seite der Hauptanwendung:
 *  Sie wird in `main.ts` VOR Bootstrap und Anmeldung gewählt und hat einen eigenen
 *  Principal. Darum zeigt kein Verweis der Hauptanwendung ohne Neuladen dorthin
 *  (`internalTarget`). */
export function isKioskPath(pathname: string, prefix: string): boolean {
  const { path } = splitTarget(relativePath(pathname, prefix));
  return path === '/kiosk';
}

/** Die Ersteinrichtung (`{prefix}/app/setup`) ist wie der Kiosk eine eigenständige Ansicht ohne
 *  Hülle und ohne Bootstrap: Es gibt zu dem Zeitpunkt noch keinen Benutzer. `main.ts` wählt sie
 *  vor allem anderen; Verweise der Hauptanwendung führen nur per Neuladen dorthin. */
export function isSetupPath(pathname: string, prefix: string): boolean {
  const { path } = splitTarget(relativePath(pathname, prefix));
  return path === '/setup';
}

/** Vollständige Browseradresse einer Ansicht der Oberfläche. */
export function appHref(prefix: string, to: string): string {
  const target = to.startsWith('/') ? to : `/${to}`;
  return `${prefix}${APP_SEGMENT}${target === '/' ? '/' : target}`;
}

/** Zerlegt `'/a/b?x=1#h'` in Pfad ohne Schlussstrich und Rest. */
export function splitTarget(to: string): { path: string; rest: string } {
  const cut = to.search(/[?#]/);
  const raw = cut === -1 ? to : to.slice(0, cut);
  const rest = cut === -1 ? '' : to.slice(cut);
  const path = raw.length > 1 ? raw.replace(/\/+$/, '') || '/' : raw || '/';
  return { path, rest };
}

/** Ziel eines Verweises, wenn er in die Oberfläche zeigt (gleiche Herkunft, unter `{prefix}/app`). */
export function internalTarget(href: string, prefix: string, origin: string): string | null {
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  const base = `${prefix}${APP_SEGMENT}`;
  if (url.pathname !== base && !url.pathname.startsWith(`${base}/`)) return null;
  if (isKioskPath(url.pathname, prefix) || isSetupPath(url.pathname, prefix)) return null;
  return `${relativePath(url.pathname, prefix)}${url.search}${url.hash}`;
}

export type Route =
  | { kind: 'home' }
  | { kind: 'room'; roomId: number }
  | { kind: 'area'; areaId: string }
  | { kind: 'page'; areaId: string; pageId: string }
  | { kind: 'account' }
  | { kind: 'help' }
  | { kind: 'login' }
  | { kind: 'unavailable'; areaId: string; pageId: string | null }
  | { kind: 'notfound'; path: string };

/** Ordnet einen Oberflächenpfad einer Ansicht zu. `visible` sind die sichtbaren Bereiche
 *  aus dem Bootstrap; bekannte, aber nicht sichtbare Ziele ergeben `unavailable`. */
export function resolveRoute(
  path: string,
  visible: readonly VisibleArea[],
): Route {
  const { path: clean } = splitTarget(path);
  if (clean === '/') return { kind: 'home' };
  const room = /^\/raeume\/(\d{1,9})$/.exec(clean);
  if (room) return { kind: 'room', roomId: Number(room[1]) };
  if (clean === '/konto') return { kind: 'account' };
  if (clean === '/hilfe') return { kind: 'help' };
  if (clean === '/login') return { kind: 'login' };
  const [, areaId, pageId, ...more] = clean.split('/');
  const known = AREAS.find((a) => a.id === areaId);
  if (!known || more.length > 0) return { kind: 'notfound', path: clean };
  if (known.id === 'uebersicht' && !pageId) return { kind: 'home' };
  if (pageId !== undefined && !known.pages.some((p) => p.id === pageId)) {
    return { kind: 'notfound', path: clean };
  }
  const area = visible.find((a) => a.id === areaId);
  if (!area) return { kind: 'unavailable', areaId, pageId: pageId ?? null };
  if (pageId === undefined) return { kind: 'area', areaId };
  if (!area.pages.some((p) => p.id === pageId)) {
    return { kind: 'unavailable', areaId, pageId };
  }
  return { kind: 'page', areaId, pageId };
}

export { areaPath };
