/**
 * Landing page — the portal cover.
 *
 * V1 opened on a full-height destination: you arrived somewhere before you navigated anywhere.
 * V2's first cut laid the same material out as the top of a scrolling page under a nav bar, and
 * the user's read was immediate — "j'avais une page d'arrivée qui était classe". The material was
 * never the problem, the staging was. So this page is centred in the viewport, the shell's nav
 * and arc strip are suppressed (`<AppShell cover>`), and the sections fade in staggered.
 *
 * It is also a directory, not just a hero: every route the app owns is reachable from here —
 * four assistants, four guided steps, two platform screens. That is what makes it a portal
 * rather than a splash.
 *
 * The tiles read the semantic model. A landing page is exactly where a hardcoded "12 000
 * clients" would survive longest without anyone noticing it had gone stale, so the numbers are
 * queried like every other figure in the app — and when the query fails the tiles say so
 * instead of rendering a confident zero. The badge dot is bound to that same query: V1's dot was
 * decoration and always green, and a status light that cannot go out is not a status light.
 */
import { Link } from 'react-router-dom';

import { AppShell } from '@/components/AppShell';
import { PERSONAS, type Source } from '@/data/personas';
import { useDax } from '@/hooks/useDax';
import { LANDING_DAX, mapLanding, type LandingStats } from '@/services/queries';

const fr = new Intl.NumberFormat('fr-FR');

/** The four steps of the guided arc, restated as doors rather than as a nav strip. */
const STEPS = [
  { to: '/detect', n: '1', label: 'Détecter', hint: 'La cohorte à risque et ce qu’elle pèse' },
  { to: '/diagnose', n: '2', label: 'Diagnostiquer', hint: 'La pression email, campagne par campagne' },
  { to: '/quantify', n: '3', label: 'Quantifier', hint: 'La valeur exposée et le manque à gagner' },
  { to: '/act', n: '4', label: 'Agir', hint: 'Les clients à rappeler, par priorité' },
];

/** The two screens that describe the platform itself rather than the business. */
const PLATFORM = [
  {
    to: '/architecture',
    icon: '🧩',
    label: 'Architecture',
    hint: 'La chaîne d’agents et l’ontologie, vérifiées dans le tenant',
  },
  {
    to: '/diagnostics',
    icon: '📡',
    label: 'Contrôle de connectivité',
    hint: 'Jetons, routes et temps de réponse',
  },
];

function Tile({
  icon,
  label,
  value,
  loading,
  failed,
}: {
  icon: string;
  label: string;
  value: number;
  loading: boolean;
  failed: boolean;
}) {
  return (
    <div className="glass portal-tile min-w-[7.5rem] rounded-2xl px-7 py-4 text-center">
      <span className="mb-1 block text-xl" aria-hidden>
        {icon}
      </span>
      <div
        className="text-2xl font-extrabold tabular-nums tracking-tight"
        style={{ color: 'var(--text-primary)' }}
      >
        {loading ? (
          <span className="opacity-40">…</span>
        ) : failed ? (
          <span className="text-sm font-normal" style={{ color: 'var(--warn-text)' }}>
            indisponible
          </span>
        ) : (
          fr.format(value)
        )}
      </div>
      <div
        className="mt-0.5 text-[0.625rem] font-bold uppercase tracking-widest"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: string }) {
  return (
    <h3
      className="mb-3 text-xs font-semibold uppercase tracking-widest"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h3>
  );
}

export function LandingPage() {
  const { data, loading, error } = useDax<LandingStats>(LANDING_DAX, mapLanding);
  const failed = !!error;
  const s = data ?? { customers: 0, campaigns: 0, segments: 0, atRisk: 0 };

  // The badge dot reports the landing query, nothing more. V1's dot was decoration and always
  // green; a status light that cannot go out is not a status light.
  const health = loading
    ? { color: '#94a3b8', text: 'connexion au modèle sémantique…' }
    : failed
      ? { color: '#f59e0b', text: 'modèle sémantique injoignable' }
      : { color: '#22c55e', text: 'Customer 360 · Fabric' };

  return (
    <AppShell wide cover>
      {/* Decoration only — `pointer-events: none`, and it carries no information. */}
      <div className="mesh-bg">
        <div className="mesh-blob" />
        <div className="mesh-blob" />
        <div className="mesh-blob" />
      </div>

      <div className="relative z-10 flex min-h-[calc(100vh-84px)] flex-col justify-center gap-9 py-12">
        <section className="portal-in text-center">
          <span
            className="glass inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-[0.6875rem] font-bold uppercase tracking-widest"
            style={{ color: 'var(--text-secondary)' }}
          >
            <span
              className="portal-dot h-1.5 w-1.5 rounded-full"
              style={{ background: health.color, boxShadow: `0 0 8px ${health.color}` }}
            />
            {health.text}
          </span>

          <h2
            className="mx-auto mt-5 max-w-3xl text-4xl font-extrabold leading-[1.1] tracking-tight sm:text-5xl"
            style={{ color: 'var(--text-primary)' }}
          >
            Le cockpit de la
            <br />
            <span className="portal-accent">connaissance client.</span>
          </h2>

          <p
            className="mx-auto mt-4 max-w-2xl text-base leading-relaxed"
            style={{ color: 'var(--text-secondary)' }}
          >
            Une vue unifiée du CRM, du marketing et du commerce. Quatre assistants interrogent le
            même socle Fabric — modèle sémantique pour les chiffres, ontologie pour les relations —
            et affichent la route réellement empruntée sous chaque réponse.
          </p>

          {error && (
            <p
              className="mx-auto mt-4 max-w-2xl rounded-lg border px-3 py-2 text-xs"
              style={{
                background: 'var(--warn-bg)',
                borderColor: 'var(--warn-border)',
                color: 'var(--warn-text)',
              }}
            >
              Les compteurs n’ont pas pu être lus : {error}
            </p>
          )}
        </section>

        <section className="portal-in portal-d1 flex flex-wrap justify-center gap-3">
          <Tile icon="👥" label="Clients" value={s.customers} loading={loading} failed={failed} />
          <Tile icon="📨" label="Campagnes" value={s.campaigns} loading={loading} failed={failed} />
          <Tile icon="🏷" label="Segments" value={s.segments} loading={loading} failed={failed} />
          <Tile
            icon="⚠️"
            label="À risque"
            value={s.atRisk}
            loading={loading}
            failed={failed}
          />
          {/* Not a model figure: this one counts the app's own personas, so it never loads. */}
          <Tile
            icon="🤖"
            label="Assistants"
            value={PERSONAS.length}
            loading={false}
            failed={false}
          />
        </section>

        <section className="portal-in portal-d2">
          <SectionLabel>Assistants — poser la question en langage naturel</SectionLabel>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {PERSONAS.map((p) => {
              // Counted per route rather than "everything that is not the ontology", which is
              // what the previous subtraction did — it would have filed the corpus questions
              // under "modèle" the moment a second subordinate became reachable.
              const count = (s: Source) => p.suggestions.filter((q) => q.expects === s).length;
              const chips: Array<[number, string]> = [
                [count('model'), 'modèle'],
                [count('ontology'), 'ontologie'],
                [count('voc'), 'verbatims'],
                [count('mixed'), 'croisé'],
              ];
              return (
                <Link
                  key={p.key}
                  to={`/agent/${p.key}`}
                  className="glass portal-card rounded-2xl p-6"
                  style={{ ['--card-accent' as string]: p.accent }}
                >
                  <span className="flex items-center gap-3">
                    <span
                      className="relative flex h-12 w-12 items-center justify-center rounded-2xl text-xl"
                      style={{ background: `${p.accent}1F` }}
                    >
                      {p.icon}
                    </span>
                    <span
                      className="text-base font-bold leading-tight"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {p.name}
                    </span>
                  </span>

                  <p
                    className="mt-3 text-sm leading-snug"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    {p.description}
                  </p>

                  {/* Derived from the persona registry, never typed in: the split is a claim
                      about which route each canned question is *expected* to take. */}
                  <span className="mt-4 flex flex-wrap items-center gap-1.5">
                    {chips
                      .filter(([n]) => n > 0)
                      .map(([n, label]) => (
                        <span
                          key={label}
                          className="portal-chip rounded-md px-2 py-1 text-[0.625rem] font-bold uppercase tracking-wide"
                        >
                          {n} {label}
                        </span>
                      ))}
                    <span className="portal-arrow ml-auto text-lg" style={{ color: p.accent }}>
                      →
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        </section>

        <section className="portal-in portal-d3 grid gap-6 lg:grid-cols-[3fr_2fr]">
          <div>
            <SectionLabel>Parcours guidé — la même donnée, dans l’ordre de la question</SectionLabel>
            <div className="grid gap-3 sm:grid-cols-2">
              {STEPS.map((st) => (
                <Link
                  key={st.to}
                  to={st.to}
                  className="glass portal-card flex items-center gap-3 rounded-xl px-4 py-3"
                >
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg font-mono text-sm font-bold"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                  >
                    {st.n}
                  </span>
                  <span className="min-w-0">
                    <span
                      className="block text-sm font-semibold"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {st.label}
                    </span>
                    <span
                      className="block truncate text-xs"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      {st.hint}
                    </span>
                  </span>
                  <span className="portal-arrow ml-auto" style={{ color: 'var(--accent)' }}>
                    →
                  </span>
                </Link>
              ))}
            </div>
          </div>

          <div>
            <SectionLabel>Plateforme</SectionLabel>
            <div className="grid gap-3">
              {PLATFORM.map((m) => (
                <Link
                  key={m.to}
                  to={m.to}
                  className="glass portal-card flex items-center gap-3 rounded-xl px-4 py-3"
                >
                  <span className="text-lg" aria-hidden>
                    {m.icon}
                  </span>
                  <span className="min-w-0">
                    <span
                      className="block text-sm font-semibold"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {m.label}
                    </span>
                    <span className="block text-xs" style={{ color: 'var(--text-muted)' }}>
                      {m.hint}
                    </span>
                  </span>
                  <span className="portal-arrow ml-auto" style={{ color: 'var(--accent)' }}>
                    →
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </section>

        <p
          className="portal-in portal-d4 border-t pt-4 text-center text-xs"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          Chiffres évalués par le modèle sémantique{' '}
          <span className="font-mono">SM_Marketing_Analytics</span>. Lecture seule, aucun agrégat
          recalculé côté application.
        </p>
      </div>
    </AppShell>
  );
}
