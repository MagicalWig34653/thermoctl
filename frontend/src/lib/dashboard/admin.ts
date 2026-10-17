// Übersicht pflegen (Server: thermoctl/web/ui_api_dashboard_admin.py): Vorlagen und die
// Übersicht beliebiger Benutzer. Rechte und Grenzen prüft der Server; hier steht nur der Weg.

import type { Api } from '../api';
import type { DashboardTarget } from './editor.svelte';
import type { Layout, Section } from './types';

export interface TemplateItem {
  id: number;
  name: string;
  tile_count: number;
}
export interface TemplateList {
  templates: TemplateItem[];
}
export interface TemplateOut {
  id: number;
  name: string;
  sections: Section[];
}

const D = '/ui-api/dashboard';

/** Eine Vorlage hat keine Version; das Layout trägt 0 und gilt als angepasst (es gibt keinen „Standard“). */
export const templateLayout = (t: TemplateOut): Layout => ({ version: 0, customized: true, sections: t.sections });

export function dashboardAdmin(api: Api) {
  return {
    templates: () => api.get<TemplateList>(`${D}/templates`),
    createTemplate: (name: string) => api.post<TemplateOut>(`${D}/templates`, { name, sections: [] }),
    createTemplateFromUser: (userId: number, name: string) => api.post<TemplateOut>(`${D}/templates/from-user/${userId}`, { name }),
    removeTemplate: (id: number) => api.delete<void>(`${D}/templates/${id}`),

    /** Übersicht eines Benutzers: Lesen, Speichern und Zurücksetzen wie bei der eigenen. */
    userTarget: (userId: number): DashboardTarget => ({
      load: () => api.get<Layout>(`${D}/users/${userId}`),
      save: (payload) => api.put<Layout>(`${D}/users/${userId}`, payload),
      reset: () => api.delete<Layout>(`${D}/users/${userId}`),
    }),
    /** Vorlage bearbeiten; der Name wird beim Speichern mitgeschickt. */
    templateTarget: (id: number, name: () => string): DashboardTarget => ({
      load: async () => templateLayout(await api.get<TemplateOut>(`${D}/templates/${id}`)),
      save: async (payload) => templateLayout(await api.put<TemplateOut>(`${D}/templates/${id}`, { name: name(), sections: payload.sections })),
    }),
    renameTemplate: async (id: number, name: string) => {
      const current = await api.get<TemplateOut>(`${D}/templates/${id}`);
      return api.put<TemplateOut>(`${D}/templates/${id}`, { name, sections: current.sections });
    },
  };
}
