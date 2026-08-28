import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { AuthPage } from '@/components/AuthPage';
import { useAuth } from '@/hooks/AuthContext';
import { ActPage } from '@/pages/ActPage';
import { AgentPage } from '@/pages/AgentPage';
import { ArchitecturePage } from '@/pages/ArchitecturePage';
import { DetectPage } from '@/pages/DetectPage';
import { DiagnosePage } from '@/pages/DiagnosePage';
import { DiagnosticsPage } from '@/pages/DiagnosticsPage';
import { LandingPage } from '@/pages/LandingPage';
import { QuantifyPage } from '@/pages/QuantifyPage';

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
        {/* The landing page leads: four personas first, the arc as a guided path underneath. */}
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
        {/* The arc is the demo: Detect → Diagnose → Quantify → Act. */}
        <Route
          path="/detect"
          element={
            <AuthGuard requireAuth={true}>
              <DetectPage />
            </AuthGuard>
          }
        />
        <Route
          path="/diagnose"
          element={
            <AuthGuard requireAuth={true}>
              <DiagnosePage />
            </AuthGuard>
          }
        />
        <Route
          path="/quantify"
          element={
            <AuthGuard requireAuth={true}>
              <QuantifyPage />
            </AuthGuard>
          }
        />
        <Route
          path="/act"
          element={
            <AuthGuard requireAuth={true}>
              <ActPage />
            </AuthGuard>
          }
        />
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
        {/* Outside the guard on purpose: a connectivity check that redirects away when
            sign-in fails would hide the failure it exists to surface. */}
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
