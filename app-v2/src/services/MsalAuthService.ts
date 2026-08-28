import { isFramed, withTimeout } from './authStartup';
import type { AuthUser, IAuthService } from './IAuthService';
import { activeAccount, ensureMsalReady, FABRIC_SCOPES, getToken, msal } from './msal';

import type { AccountInfo } from '@azure/msal-browser';

function toUser(a: AccountInfo): AuthUser {
  const email = a.username ?? '';
  return { id: a.localAccountId ?? a.homeAccountId ?? email, email, name: a.name || email.split('@')[0] };
}

/**
 * IAuthService backed by Entra directly, instead of the Rayfin brokered session.
 *
 * The template ships MockAuthService (localhost) and RayfinAuthService (Fabric brokered).
 * Neither can produce a token for api.fabric.microsoft.com or ai.azure.com, and this app's
 * entire purpose is reading those two planes — so sign-in and data access are unified on one
 * identity here rather than running two parallel auth stacks whose sessions could disagree
 * about who the user is.
 */
export class MsalAuthService implements IAuthService {
  readonly fabricAuthEnabled = true;

  async signIn(): Promise<AuthUser> {
    await ensureMsalReady();
    // Sign in and consent to the Fabric scopes in the same gesture, so the first data call
    // does not trigger a second popup the user did not ask for.
    const r = await msal.loginPopup({ scopes: FABRIC_SCOPES });
    if (r.account) msal.setActiveAccount(r.account);
    const a = activeAccount();
    if (!a) throw new Error('Sign-in returned no account.');
    return toUser(a);
  }

  async signOut(): Promise<void> {
    await ensureMsalReady();
    const account = activeAccount() ?? undefined;
    await msal.logoutPopup({ account });
  }

  async getCurrentUser(): Promise<AuthUser | null> {
    await ensureMsalReady();
    const a = activeAccount();
    return a ? toUser(a) : null;
  }

  /**
   * Startup path: recover a session without any UI.
   *
   * Runs inside and outside the Fabric iframe. Returning `null` here is a normal outcome
   * (not an error) — it just means the user has to click Sign in.
   *
   * Every await here is bounded. This method is the only thing standing between the app and
   * an unbounded spinner, and it runs against a host (the Fabric portal) that cannot be
   * reproduced on a dev machine, so "this call always settles" is an assumption rather than
   * something observed.
   */
  async initEmbeddedAuth(): Promise<AuthUser | null> {
    await withTimeout(ensureMsalReady(), 'msal.initialize + handleRedirectPromise');

    if (activeAccount()) {
      try {
        await withTimeout(getToken(FABRIC_SCOPES, false), 'acquireTokenSilent');
        return await this.getCurrentUser();
      } catch {
        return null;
      }
    }

    // ssoSilent authenticates through a hidden iframe pointed at Entra. Nested inside the
    // portal's own iframe that request is third-party — partitioned storage, and in a private
    // window no session cookie at all — so it cannot succeed here, and an attempt that cannot
    // succeed is only a chance to stall. Fall straight through to the Sign in button.
    if (isFramed()) return null;

    try {
      const r = await withTimeout(msal.ssoSilent({ scopes: FABRIC_SCOPES }), 'ssoSilent');
      if (r.account) msal.setActiveAccount(r.account);
      return await this.getCurrentUser();
    } catch {
      return null;
    }
  }
}
