import { getContext, setContext } from 'svelte';
import type { Api } from './api';
import type { Connection } from './connection.svelte';
import type { Appearance } from './appearance.svelte';
import type { Router } from './router.svelte';
import type { Session } from './session.svelte';

export interface AppContext {
  prefix: string;
  api: Api;
  router: Router;
  session: Session;
  appearance: Appearance;
  connection: Connection;
}

const KEY = Symbol('thermoctl-app');

export const provideApp = (value: AppContext) => setContext(KEY, value);
export const useApp = () => getContext<AppContext>(KEY);
