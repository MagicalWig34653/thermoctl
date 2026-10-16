// Formen der Antworten von /ui-api/access/* und /ui-api/account/* (thermoctl/web/ui_api_access.py).

export interface Grant {
  code: string;
  zone_id: number | null;
}

export interface UserRow {
  id: number;
  username: string;
  display_name: string;
  is_active: boolean;
  is_self: boolean;
  group_id: number | null;
  created_at: string;
  last_login_at: string | null;
}
export interface GroupRef {
  id: number;
  name: string;
}
export interface UsersData {
  users: UserRow[];
  groups: GroupRef[];
}

export interface PermissionInfo {
  code: string;
  description: string;
  zone_scoped: boolean;
}
export interface PermissionArea {
  title: string;
  hint: string;
  permissions: PermissionInfo[];
}
export interface GroupRow {
  id: number;
  name: string;
  description: string | null;
  is_builtin: boolean;
  ui_profile: string;
  member_count: number;
  permissions: Grant[];
  summary: string[];
}
export interface ZoneRef {
  id: number;
  name: string;
}
export interface GroupsData {
  groups: GroupRow[];
  areas: PermissionArea[];
  zones: ZoneRef[];
  ui_profiles: string[];
}

export interface TokenRow {
  id: number;
  name: string;
  prefix: string;
  state: string;
  source: string;
  client_name: string | null;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  permissions: Grant[];
  effective: Grant[];
}
export interface AvailableGrant {
  code: string;
  zone_id: number | null;
  description: string;
}
export interface TokensData {
  tokens: TokenRow[];
  available: AvailableGrant[];
  presets: Record<string, string[]>;
}
/** Einzige Antwort mit Klartext. Der Aufrufer reicht `plaintext` sofort an einen SecretHolder. */
export interface IssuedToken {
  token: TokenRow;
  plaintext: string;
}

export interface KioskRow {
  id: number;
  name: string;
  prefix: string;
  state: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  zones: ZoneRef[];
  control_allowed: boolean;
}
export interface KioskData {
  tokens: KioskRow[];
  zones: ZoneRef[];
  /** Zeitzone der Anlage; Zeitpunkte der Zugänge werden darin angezeigt. */
  timezone: string;
}
export interface IssuedKiosk {
  token: KioskRow;
  plaintext: string;
}

export interface Account {
  user_id: number;
  username: string;
  display_name: string;
  ui_profile: string;
  created_at: string;
  last_login_at: string | null;
  passkeys_available: boolean;
}
export interface SessionRow {
  id: number;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
  current: boolean;
}
export interface PasskeyRow {
  id: number;
  label: string;
  created_at: string;
  last_used_at: string | null;
}
export interface PasskeysData {
  available: boolean;
  passkeys: PasskeyRow[];
}
