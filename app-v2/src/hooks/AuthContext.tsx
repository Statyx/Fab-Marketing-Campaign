import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { StartupTimeoutError, withTimeout } from '@/services/authStartup';
import { type AuthUser, type IAuthService } from '@/services/IAuthService';

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  /**
   * Set when startup gave up waiting instead of completing. Rendered by {@link AuthPage} so a
   * stalled dependency shows up as a sentence on screen rather than as a spinner that never ends.
   */
  startupWarning: string | null;
  signIn: () => Promise<AuthUser>;
  signOut: () => Promise<void>;
  isAuthenticated: boolean;
  fabricAuthEnabled: boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
  authService: IAuthService;
}

export function AuthProvider({ children, authService }: AuthProviderProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startupWarning, setStartupWarning] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Bounded a second time here on purpose. MsalAuthService bounds its own steps, but this
    // provider is handed whichever IAuthService bootstrapAuth() chose, and the guarantee the
    // UI needs — "loading always clears" — has to hold for all of them, not just that one.
    withTimeout(
      authService
        .initEmbeddedAuth()
        .then((embedded) => embedded ?? authService.getCurrentUser()),
      'startup auth'
    )
      .then((current) => {
        if (!cancelled && current) setUser(current);
      })
      .catch((err) => {
        if (cancelled) return;
        setUser(null);
        if (err instanceof StartupTimeoutError) {
          setStartupWarning(
            `La reprise de session automatique n'a pas abouti (${err.step} — ${err.ms} ms). ` +
              'Connectez-vous manuellement.'
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authService]);

  const signIn = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const loggedInUser = await authService.signIn();
      setUser(loggedInUser);
      return loggedInUser;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Login failed';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [authService]);

  const signOut = useCallback(async () => {
    try {
      await authService.signOut();
      setUser(null);
      setError(null);
    } catch (err) {
      console.error('Logout error:', err);
    }
  }, [authService]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      error,
      startupWarning,
      signIn,
      signOut,
      isAuthenticated: !!user,
      fabricAuthEnabled: authService.fabricAuthEnabled,
    }),
    [user, loading, error, startupWarning, signIn, signOut, authService]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
