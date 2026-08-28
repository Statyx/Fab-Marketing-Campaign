/**
 * Fabric REST client — browser-side, read-only.
 *
 * This app is hosted on a Sweden Central capacity but reads a data plane that stays in the
 * existing West US 3 workspace. Nothing here writes: the V2 app is an additional consumer of
 * that workspace, never a second owner of its items.
 */
import { FABRIC_SCOPES, getToken } from './msal';

const API = 'https://api.fabric.microsoft.com/v1';

export const dataWorkspaceId = import.meta.env.VITE_DATA_WORKSPACE_ID;

export interface FabricItem {
  id: string;
  displayName: string;
  type: string;
  description?: string;
}

async function fabricGet<T>(path: string): Promise<T> {
  const token = await getToken(FABRIC_SCOPES);
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await res.text();
  if (!res.ok) throw new Error(`Fabric ${res.status}: ${body.slice(0, 600)}`);
  return JSON.parse(body) as T;
}

/** Items of the data workspace — the cheapest call that proves a real authenticated read. */
export async function listItems(workspaceId = dataWorkspaceId): Promise<FabricItem[]> {
  if (!workspaceId) throw new Error('VITE_DATA_WORKSPACE_ID is not set.');
  const r = await fabricGet<{ value: FabricItem[] }>(`/workspaces/${workspaceId}/items`);
  return r.value ?? [];
}
