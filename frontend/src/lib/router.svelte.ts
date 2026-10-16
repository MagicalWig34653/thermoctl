// Minimaler Router ohne Hash: die Oberfläche liegt unter `{prefix}/app/...`, der Server
// liefert für jeden Direktlink dieselbe Hülle aus (thermoctl/web/spa_views.py). Die reine
// Pfadlogik steht in `paths.ts`, damit sie ohne Svelte testbar ist.

import { appHref, internalTarget, relativePath, splitTarget } from './paths';

export { APP_SEGMENT, relativePath } from './paths';

export function createRouter(prefix: string) {
  const current = () => `${relativePath(window.location.pathname, prefix)}`;
  let path = $state(splitTarget(current()).path);
  let search = $state(window.location.search);

  function sync() {
    path = splitTarget(current()).path;
    search = window.location.search;
    window.scrollTo(0, 0);
  }
  window.addEventListener('popstate', sync);

  function navigate(to: string, options: { replace?: boolean } = {}) {
    const href = appHref(prefix, to);
    if (options.replace) window.history.replaceState({}, '', href);
    else window.history.pushState({}, '', href);
    sync();
  }

  return {
    get path() {
      return path;
    },
    get search() {
      return search;
    },
    navigate,
    /** Klick auf einen Verweis in die Oberfläche: ohne Neuladen navigieren. Alles andere
     *  (Strg/Cmd-Klick, neues Fenster, Verweise auf alte HTML-Seiten) bleibt dem Browser. */
    handleClick(event: MouseEvent) {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest?.('a');
      if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
      const to = internalTarget(anchor.href, prefix, window.location.origin);
      if (to === null) return;
      event.preventDefault();
      navigate(to);
    },
  };
}

export type Router = ReturnType<typeof createRouter>;
