import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge';

/**
 * Entry point for the MSAL redirect landing page (`blank.html`).
 *
 * MSAL v5 no longer polls the popup's URL for the auth response: the opener waits on a
 * `BroadcastChannel` keyed by the request state, and the landing page is responsible for
 * publishing the response onto it. `broadcastResponseToMainFrame()` parses the response out
 * of this URL, posts it, and closes the window.
 *
 * A landing page that does nothing is therefore not neutral — it is a dead end. The opener
 * waits out its timeout and rejects with `BrowserAuthError: timed_out`
 * (subcode `redirect_bridge_timeout`), which reads like a network or consent problem and is
 * neither. The same channel carries the silent-renewal response from the hidden iframe.
 */
broadcastResponseToMainFrame().catch((error: unknown) => {
  // The window normally closes itself, so anything rendered here is already a failure path.
  const detail = error instanceof Error ? error.message : String(error);
  document.body.textContent = `Sign-in could not be completed: ${detail}`;
});
