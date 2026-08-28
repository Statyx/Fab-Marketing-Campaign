import { createRoot } from 'react-dom/client';

import App from '@/App';
import { AuthProvider } from '@/hooks/AuthContext';
import { bootstrapAuth } from '@/services/bootstrap';

import './main.css';

/**
 * Does this document exist only to carry an Entra auth response back to MSAL?
 *
 * `blank.html` is the configured landing page, but static hosts commonly rewrite unknown
 * paths to index.html — and if that happened, mounting the router here would navigate and
 * discard the response before MSAL could read it. Handle it exactly as the landing page
 * does, and never mount the app on top of it.
 */
const isAuthResponse = /(^|[#&?])(code|error|state|id_token)=/.test(window.location.hash);

if (isAuthResponse) {
  void import('@azure/msal-browser/redirect-bridge')
    .then(({ broadcastResponseToMainFrame }) => broadcastResponseToMainFrame())
    .catch(() => {
      /* The opener surfaces the failure; this window has no UI to report it with. */
    });
} else {
  const authService = bootstrapAuth();

  createRoot(document.getElementById('root')!).render(
    <AuthProvider authService={authService}>
      <App />
    </AuthProvider>
  );
}
