/**
 * Entra (MSAL) token acquisition for the two back-end planes this app reads.
 *
 * Why this exists at all: Rayfin's Fabric auth returns an *opaque Rayfin session*, not an
 * Entra token ("Session objects are opaque. Gate UI logic on isAuthenticated." — Rayfin docs).
 * That session cannot authorize api.fabric.microsoft.com or ai.azure.com, so the app runs its
 * own MSAL to obtain real delegated tokens for the data plane.
 *
 * Two audiences, therefore two token requests — a single token cannot span both:
 *   https://api.fabric.microsoft.com  (Fabric REST / GraphQL)
 *   https://ai.azure.com             (Foundry agents)
 *
 * Power BI Service exposes NO `user_impersonation` scope to third-party apps; it uses granular
 * scopes, which is why FABRIC_SCOPES lists them individually. (`az account get-access-token`
 * returns user_impersonation only because the Azure CLI is a pre-authorized first-party client
 * — that is not a precedent a custom registration can follow.)
 */
import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
  type Configuration,
} from '@azure/msal-browser';

export const FABRIC_SCOPES = [
  'https://api.fabric.microsoft.com/Item.Execute.All',
  'https://api.fabric.microsoft.com/Item.Read.All',
  'https://api.fabric.microsoft.com/Workspace.Read.All',
  'https://api.fabric.microsoft.com/Dataset.Read.All',
];

export const FOUNDRY_SCOPES = ['https://ai.azure.com/user_impersonation'];

/**
 * Power BI REST (`executeQueries`) lives on a different audience from the Fabric REST API,
 * even though both resolve to the same service principal — so it needs its own token request.
 */
export const POWERBI_SCOPES = ['https://analysis.windows.net/powerbi/api/Dataset.Read.All'];

const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID;

export const msalConfigured = Boolean(clientId && tenantId);

/**
 * Both the popup and the silent-renewal iframe land here.
 *
 * It must NOT be the app root: coming back to '/' boots the whole SPA inside the popup, the
 * router immediately navigates to '/auth', and that navigation throws away the response
 * fragment MSAL is waiting for — observed as `BrowserAuthError: timed_out` with the popup
 * sitting on the app's own sign-in screen.
 */
const redirectUri = `${window.location.origin}/blank.html`;

const config: Configuration = {
  auth: {
    clientId: clientId ?? '',
    authority: `https://login.microsoftonline.com/${tenantId ?? 'common'}`,
    redirectUri,
  },
  cache: { cacheLocation: 'sessionStorage' },
};

export const msal = new PublicClientApplication(config);

let ready: Promise<void> | null = null;

/** MSAL v3+ requires an explicit initialize() before any other call. */
export function ensureMsalReady(): Promise<void> {
  if (!ready) {
    ready = msal.initialize().then(async () => {
      await msal.handleRedirectPromise();
      const [first] = msal.getAllAccounts();
      if (first && !msal.getActiveAccount()) msal.setActiveAccount(first);
    });
  }
  return ready;
}

export function activeAccount(): AccountInfo | null {
  return msal.getActiveAccount() ?? msal.getAllAccounts()[0] ?? null;
}

/**
 * Silent-first token acquisition, falling back to a popup.
 *
 * `allowPopup: false` is used by the embedded/startup path, where a popup would either be
 * blocked (no user gesture) or hijack the page load.
 */
export async function getToken(
  scopes: string[],
  allowPopup = true
): Promise<string> {
  await ensureMsalReady();
  const account = activeAccount();

  if (account) {
    try {
      const r = await msal.acquireTokenSilent({ scopes, account });
      return r.accessToken;
    } catch (err) {
      if (!(err instanceof InteractionRequiredAuthError) || !allowPopup) throw err;
    }
  } else if (!allowPopup) {
    throw new Error('No signed-in account and interaction is not allowed here.');
  }

  const r = await msal.acquireTokenPopup({ scopes });
  if (r.account) msal.setActiveAccount(r.account);
  return r.accessToken;
}

/**
 * Decode a JWT payload for display only.
 *
 * This never validates the signature and must never gate behaviour — it exists so the
 * diagnostics screen can show which audience and scopes were actually granted, which is the
 * difference between "a token came back" and "the right token came back".
 */
export function decodeJwt(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(decodeURIComponent(escape(json)));
  } catch {
    return null;
  }
}
