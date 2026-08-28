/**
 * Application shell.
 *
 * V1's structure, restored: the four agent personas lead, and the Detect → Diagnose → Quantify
 * → Act arc sits underneath as a guided path. The arc is still the argument, but it is one
 * reading of the data among several — putting it in the only navigation made the app feel like
 * a slideshow with four slides.
 *
 * The header stays dark in both themes, as in V1: it is the one fixed anchor while the page
 * below repaints between light and dark.
 *
 * The footer states where every figure comes from — which semantic model answered, and that the
 * app recomputes nothing. The hosting region is deliberately NOT on screen: it is infrastructure
 * trivia on a demo, and it belongs on the diagnostics page, where connectivity is the subject.
 */
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

import { ThemeToggle } from '@/components/ThemeToggle';
import { PERSONAS } from '@/data/personas';
import { useAuth } from '@/hooks/AuthContext';

const STEPS = [
  { to: '/detect', n: '1', label: 'Détecter' },
  { to: '/diagnose', n: '2', label: 'Diagnostiquer' },
  { to: '/quantify', n: '3', label: 'Quantifier' },
  { to: '/act', n: '4', label: 'Agir' },
];

export function AppShell({
  title,
  intro,
  children,
  wide = false,
  cover = false,
}: {
  title?: string;
  intro?: string;
  children: ReactNode;
  /** Landing and chat want the full width; the arc screens read better in a column. */
  wide?: boolean;
  /**
   * Cover mode, for the landing only: the persona pills and the guided-arc strip come out of
   * the header, and the page owns the viewport.
   *
   * Not cosmetic. On the landing every one of those links is *also* a card below, so the strip
   * was a second, smaller copy of the page's whole purpose sitting on top of it — which is
   * precisely what turned a front page into "the first screen of an app". V1 hid its nav here
   * for the same reason.
   */
  cover?: boolean;
}) {
  const { user, signOut } = useAuth();

  return (
    <div className="flex min-h-screen flex-col" style={{ background: 'var(--bg-secondary)' }}>
      <header
        className="sticky top-0 z-30 border-b border-white/10"
        style={{
          background: 'var(--header-bg)',
          backdropFilter: 'blur(24px)',
          WebkitBackdropFilter: 'blur(24px)',
        }}
      >
        <div className="mx-auto flex h-[84px] max-w-[1400px] items-center gap-6 px-6">
          <NavLink to="/" className="flex shrink-0 items-center gap-3">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-xl text-lg"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              ⚡
            </span>
            <span className="leading-tight">
              <span className="block text-sm font-semibold text-white">Customer 360 Cockpit</span>
              <span className="block text-xs text-slate-400">
                Marketing &amp; churn — Fabric V2
              </span>
            </span>
          </NavLink>

          <nav className="ml-auto flex items-center gap-1">
            {!cover &&
              PERSONAS.map((p) => (
                <NavLink
                  key={p.key}
                  to={`/agent/${p.key}`}
                  className={({ isActive }) =>
                    `flex items-center gap-2 rounded-full px-3 py-2 text-sm transition ${
                      isActive ? 'text-white' : 'text-slate-400 hover:bg-white/5 hover:text-white'
                    }`
                  }
                  style={({ isActive }) => (isActive ? { background: p.accent } : undefined)}
                >
                  <span aria-hidden>{p.icon}</span>
                  <span className="hidden font-medium lg:inline">{p.name}</span>
                </NavLink>
              ))}
          </nav>

          <div className="flex shrink-0 items-center gap-3 border-l border-white/10 pl-4">
            <ThemeToggle />
            {user && (
              <div className="hidden text-right leading-tight sm:block">
                <span className="block max-w-[10rem] truncate text-xs text-slate-300">
                  {user.name}
                </span>
                <button
                  onClick={() => void signOut()}
                  className="text-xs text-slate-500 hover:text-slate-300"
                >
                  Se déconnecter
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Secondary: the guided arc. Still one click away, no longer the only way in. */}
        {!cover && (
          <div className="border-t border-white/5">
          <div className="mx-auto flex max-w-[1400px] items-center gap-2 px-6 py-2">
            <span className="mr-1 text-[11px] uppercase tracking-wide text-slate-500">
              Parcours guidé
            </span>
            {STEPS.map((s) => (
              <NavLink
                key={s.to}
                to={s.to}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition ${
                    isActive
                      ? 'bg-white/15 text-white'
                      : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                  }`
                }
              >
                <span className="font-mono text-[10px] opacity-60">{s.n}</span>
                {s.label}
              </NavLink>
            ))}
            <NavLink
              to="/architecture"
              className={({ isActive }) =>
                `ml-auto text-xs transition ${
                  isActive ? 'text-slate-200' : 'text-slate-500 hover:text-slate-300'
                }`
              }
            >
              Architecture
            </NavLink>
            <NavLink
              to="/diagnostics"
              className="text-xs text-slate-500 hover:text-slate-300"
            >
              Contrôle de connectivité
            </NavLink>
          </div>
          </div>
        )}
      </header>

      <main className="relative flex-1 overflow-x-hidden">
        <div
          className={`relative z-10 mx-auto px-6 ${cover ? 'py-0' : 'py-8'} ${
            wide ? 'max-w-[1400px]' : 'max-w-6xl'
          }`}
        >
          {title && (
            <header>
              <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
                {title}
              </h1>
              {intro && (
                <p className="mt-1 max-w-3xl text-sm" style={{ color: 'var(--text-secondary)' }}>
                  {intro}
                </p>
              )}
            </header>
          )}

          <div className={title ? 'mt-8' : ''}>{children}</div>

          {/* The cover restates this in its own footing, sized for a front page. */}
          {!cover && (
            <footer
              className="mt-12 border-t pt-4 text-xs"
              style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
            >
              Chiffres évalués par le modèle sémantique{' '}
              <span className="font-mono">SM_Marketing_Analytics</span>. Lecture seule, aucun
              agrégat recalculé côté application.
            </footer>
          )}
        </div>
      </main>
    </div>
  );
}
