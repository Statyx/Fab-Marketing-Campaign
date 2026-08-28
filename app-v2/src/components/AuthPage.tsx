import { useState } from 'react';

import { useAuth } from '@/hooks/AuthContext';
import { isFramed } from '@/services/authStartup';

const msLogo = (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    viewBox="0 0 21 21"
    className="mr-2"
  >
    <rect x="1" y="1" width="9" height="9" fill="#f25022" />
    <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
    <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
    <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
  </svg>
);

export function AuthPage() {
  const { signIn, fabricAuthEnabled, startupWarning } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Read once at render: whether the portal is hosting us decides which sign-in path can work.
  const framed = isFramed();

  const handleSignIn = async () => {
    setError(null);
    setIsLoading(true);

    try {
      await signIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sign in.');
    } finally {
      setIsLoading(false);
    }
  };

  const buttonLabel = isLoading
    ? fabricAuthEnabled
      ? 'Opening Fabric...'
      : 'Signing in...'
    : 'Sign in with Microsoft';

  return (
    <div className="auth-bg relative min-h-screen flex flex-col overflow-hidden">
      {/* Decorative background shapes. Tinted 500s at low alpha rather than 100s at high alpha:
          a light-100 haze reads as a soft glow on white but as a washed-out smear on the dark
          page, whereas these read as a glow in both themes. */}
      <div className="pointer-events-none absolute -top-24 -right-24 h-96 w-96 rounded-full bg-blue-500/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -left-32 h-[500px] w-[500px] rounded-full bg-indigo-500/20 blur-3xl" />

      <div className="relative flex flex-1 items-center justify-center p-4">
        <div className="w-full max-w-sm">
          <div className="glass rounded-3xl p-8 shadow-xl">
            <div className="mb-8 text-center">
              <h1 className="text-2xl font-bold text-gray-900">Customer 360 Cockpit</h1>
              <p className="mt-2 text-sm text-gray-500">
                Sign in to get started.
              </p>
            </div>

            <button
              type="button"
              onClick={handleSignIn}
              disabled={isLoading}
              className="flex w-full items-center justify-center rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-3 text-sm font-medium text-white shadow-md shadow-blue-600/25 transition-all hover:shadow-lg hover:shadow-blue-600/30 hover:brightness-110 disabled:opacity-50 disabled:shadow-none"
            >
              {msLogo}
              {buttonLabel}
            </button>

            {error && (
              <p className="mt-3 text-center text-sm text-red-600">{error}</p>
            )}

            {startupWarning && (
              <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-center text-xs text-amber-800">
                {startupWarning}
              </p>
            )}

            {framed && (
              <div className="mt-5 border-t border-gray-200 pt-4">
                <p className="text-xs leading-relaxed text-gray-600">
                  L&apos;application est ouverte <strong>dans le portail Fabric</strong>. La
                  connexion Entra passe par une fenêtre popup, que le portail peut bloquer. Si
                  le bouton ci-dessus ne produit rien, ouvrez l&apos;application dans un onglet
                  dédié : c&apos;est le chemin vérifié.
                </p>
                <a
                  href={window.location.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 flex w-full items-center justify-center rounded-xl border border-blue-600 px-4 py-2.5 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-50"
                >
                  Ouvrir dans un nouvel onglet ↗
                </a>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
