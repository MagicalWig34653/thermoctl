// Minimaler Router ohne Hash: die Oberfläche liegt unter `{prefix}/app/...`, der Server
// liefert für jeden Direktlink dieselbe Hülle aus (thermoctl/web/spa_views.py).

export const APP_SEGMENT = '/app';

/** Pfad innerhalb der Oberfläche (`/zonen`), aus dem vollen Browserpfad abgeleitet. */
export function relativePath(pathname: string, prefix: string): string {
  const base = `${prefix}${APP_SEGMENT}`;
  if (!pathname.startsWith(base)) return '/';
  return pathname.slice(base.length) || '/';
}

export function createRouter(prefix: string) {
  let path = $state(relativePath(window.location.pathname, prefix));
  window.addEventListener('popstate', () => {
    path = relativePath(window.location.pathname, prefix);
  });
  return {
    get path() {
      return path;
    },
    navigate(to: string) {
      window.history.pushState({}, '', `${prefix}${APP_SEGMENT}${to}`);
      path = to;
    },
  };
}
