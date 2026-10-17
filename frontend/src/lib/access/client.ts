// Aufrufe der Zugangs-Schnittstelle. Schreibende Aufrufe laufen genau einmal: ein zweiter
// Versuch nach unklarem Ausgang könnte einen zweiten Token ausstellen oder eine Sitzung beenden.
import type { Api } from '../api';
import type {
  Account, Grant, GroupRow, GroupsData, IssuedKiosk, IssuedToken, KioskData, KioskRow, PasskeysData,
  SessionRow, TokenRow, TokensData, UserRow, UsersData,
} from './types';

const A = '/ui-api/access';
const C = '/ui-api/account';

export function createAccessClient(api: Api) {
  return {
    users: () => api.get<UsersData>(`${A}/users`),
    createUser: (body: { username: string; display_name: string; password: string; group_id: number | null; template_id?: number | null }) =>
      api.post<UserRow>(`${A}/users`, body),
    setActive: (id: number, active: boolean) => api.put<UserRow>(`${A}/users/${id}/active`, { active }),
    setUserGroup: (id: number, groupId: number | null) => api.put<UserRow>(`${A}/users/${id}/group`, { group_id: groupId }),
    setUserPassword: (id: number, password: string) => api.put<{ ok: boolean }>(`${A}/users/${id}/password`, { password }),

    groups: () => api.get<GroupsData>(`${A}/groups`),
    createGroup: (body: { name: string; description: string | null; ui_profile: string }) => api.post<GroupRow>(`${A}/groups`, body),
    setUiProfile: (id: number, uiProfile: string) => api.put<GroupRow>(`${A}/groups/${id}/ui-profile`, { ui_profile: uiProfile }),
    deleteGroup: (id: number) => api.delete<{ ok: boolean }>(`${A}/groups/${id}`),
    setPermissions: (id: number, permissions: Grant[]) => api.put<GroupRow>(`${A}/groups/${id}/permissions`, { permissions }),

    tokens: () => api.get<TokensData>(`${A}/tokens`),
    issueToken: (body: { name: string; valid_days: number | null; permissions?: Grant[]; preset?: string }) =>
      api.post<IssuedToken>(`${A}/tokens`, body),
    setTokenPermissions: (id: number, permissions: Grant[]) => api.put<TokenRow>(`${A}/tokens/${id}/permissions`, { permissions }),
    revokeToken: (id: number) => api.post<TokenRow>(`${A}/tokens/${id}/revoke`),

    kioskTokens: () => api.get<KioskData>(`${A}/kiosk-tokens`),
    issueKiosk: (body: { name: string; zone_ids: number[]; control_allowed: boolean; valid_days: number | null }) =>
      api.post<IssuedKiosk>(`${A}/kiosk-tokens`, body),
    revokeKiosk: (id: number) => api.post<KioskRow>(`${A}/kiosk-tokens/${id}/revoke`),

    account: () => api.get<Account>(C),
    changePassword: (current: string, next: string) =>
      api.put<{ ok: boolean }>(`${C}/password`, { current_password: current, new_password: next }),
    sessions: () => api.get<SessionRow[]>(`${C}/sessions`),
    revokeOthers: () => api.post<{ ended: number }>(`${C}/sessions/revoke-others`),
    revokeSession: (id: number) => api.post<{ ok: boolean }>(`${C}/sessions/${id}/revoke`),
    passkeys: () => api.get<PasskeysData>(`${C}/passkeys`),
    removePasskey: (id: number) => api.delete<{ ok: boolean }>(`${C}/passkeys/${id}`),
  };
}

export type AccessClient = ReturnType<typeof createAccessClient>;
