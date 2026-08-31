import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { AuthPage } from '@/components/AuthPage';
import { useAuth } from '@/hooks/AuthContext';
import { AgentPage } from '@/pages/AgentPage';
import { ArchitecturePage } from '@/pages/ArchitecturePage';
import { DiagnosticsPage } from '@/pages/DiagnosticsPage';
import { LandingPage } from '@/pages/LandingPage';

function AuthGuard({
  children,
  requireAuth,
}: {
  children: React.ReactNode;
  requireAuth: boolean;
}) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (requireAuth && !isAuthenticated) return <Navigate to="/auth" replace />;
  if (!requireAuth && isAuthenticated) return <Navigate to="/" replace />;

  return <>{children}</>;
}

function App() {
  return (
    <BrowserRouter>
      {/* ensure all new routes require auth */}
      <Routes>
        <Route
          path="/auth"
          element={
            <AuthGuard requireAuth={false}>
              <AuthPage />
            </AuthGuard>
          }
        />
        {/* The landing page leads: four personas, each holding its own figures and its own agent. */}
        <Route
          path="/"
          element={
            <AuthGuard requireAuth={true}>
              <LandingPage />
            </AuthGuard>
          }
        />
        <Route
          path="/agent/:key"
          element={
            <AuthGuard requireAuth={true}>
              <AgentPage />
            </AuthGuard>
          }
        />
        {/* The four "Parcours guidé" URLs. Their screens were merged into the personas that
            already owned those subjects; the paths are kept as redirects rather than left to
            404, because they are in the demo's muscle memory and in a bookmark or two. */}
        <Route path="/detect" element={<Navigate to="/agent/retention" replace />} />
        <Route path="/diagnose" element={<Navigate to="/agent/marketing" replace />} />
        <Route path="/quantify" element={<Navigate to="/agent/commerce" replace />} />
        <Route path="/act" element={<Navigate to="/agent/direction" replace />} />
        {/* Inside the guard: the page asks Fabric and Foundry what exists, so it needs a token.
            Without one every box would land in 'non vérifié' and the picture would say nothing. */}
        <Route
          path="/architecture"
          element={
            <AuthGuard requireAuth={true}>
              <ArchitecturePage />
            </AuthGuard>
          }
        />
        {/* Unlisted on purpose, and kept on purpose. No link points here any more: it proved a
            custom SPA registration could obtain delegated Fabric and Foundry tokens in a browser,
            which is settled, and on a demo it advertised a test instrument beside the page that
            explains the product. But it is the only screen that reports *which* link in the chain
            failed — and it is deliberately OUTSIDE the auth guard, so it still answers when
            sign-in itself is what broke. Deleting it would mean a rebuild and a redeploy to get a
            diagnosis back, at the exact moment there is no time for either. */}
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
