/**
 * One persona = one cockpit: the figures and the conversation about them, on the same screen.
 *
 * ── Why the two used to be apart, and why that was the defect ───────────────────────────
 * The charts lived on four "Parcours guidé" screens named after a method (Détecter, Diagnostiquer,
 * Quantifier, Agir) while the personas held a chat with no data at all. Same four topics, two
 * navigations, and nothing on the persona screen said that live DAX ran anywhere in the product —
 * so the app was read as a chatbot. The arc screens were merged in here and deleted.
 *
 * Two claims are kept apart on this screen, because collapsing them is how a demo starts lying:
 *
 *  - **Expected source.** Each suggested question carries the route it was written for. That
 *    badge is visible *before* the question is sent, so the audience can see the intent.
 *  - **Actual route.** After the answer, the connection names of the A2A calls that really
 *    fired are shown. The agent decides its own routing at runtime; this is the only statement
 *    of what happened, and it is allowed to contradict the expectation.
 *
 * The trace below the pending answer is equally careful. Only the supervisor's own hops are
 * observable from here — what happens inside the front door (MCP → data agent → DAX) arrives
 * with the A2A result and is never watched live. So the inner step is labelled as declared, not
 * measured, rather than animated as if we were seeing it.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, Navigate } from 'react-router-dom';

import { AppShell } from '@/components/AppShell';
import { Markdown } from '@/components/Markdown';
import { PersonaPanels } from '@/components/PersonaPanels';
import { personaByKey, type Source } from '@/data/personas';
import { askSupervisor, foundryConfigured } from '@/services/foundry';

interface Turn {
  id: number;
  question: string;
  expected: Source | null;
  answer: string | null;
  toolsFired: string[];
  error: string | null;
  seconds: number;
}

/**
 * How each expected route is presented.
 *
 * Kept as one table rather than a boolean, because there are now four routes and the previous
 * shape — `const ontology = source === 'ontology'` — silently rendered everything that was not
 * the ontology as the semantic model, which would have labelled a corpus question "DAX".
 */
const SOURCE_STYLE: Record<Source, { label: string; icon: string; tint: string; ink: string }> = {
  model: {
    label: 'Modèle sémantique · DAX',
    icon: '📊',
    tint: 'rgba(124,92,230,0.12)',
    ink: 'var(--accent)',
  },
  ontology: { label: 'Ontologie · graphe', icon: '🕸', tint: 'rgba(2,113,128,0.12)', ink: '#027180' },
  voc: { label: 'Verbatims clients', icon: '💬', tint: 'rgba(137,102,16,0.14)', ink: '#7a5a0e' },
  mixed: {
    label: 'Chiffres + verbatims',
    icon: '🔀',
    tint: 'rgba(134,60,65,0.12)',
    ink: '#863C41',
  },
};

function SourceChip({ source }: { source: Source }) {
  const s = SOURCE_STYLE[source];
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ background: s.tint, color: s.ink }}
    >
      {s.icon} {s.label}
    </span>
  );
}

/** What the supervisor actually did, read off the response items. */
function RoutingBadges({ tools }: { tools: string[] }) {
  if (tools.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
        ⚠ réponse non sourcée — aucun sous-agent n’a été appelé
      </span>
    );
  }
  // Two *distinct* connection names means the two subordinates were both consulted, so the
  // answer contains something neither could have produced alone. Counted on the names rather
  // than on `tools.length`, because one subordinate called twice is still one source.
  const bothConsulted = new Set(tools).size > 1;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
        Route réelle :
      </span>
      {tools.map((t, i) => (
        <span
          key={`${t}-${i}`}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[11px]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
        >
          {t}
        </span>
      ))}
      {bothConsulted && (
        <span
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold"
          style={{ background: 'rgba(134,60,65,0.12)', color: '#863C41' }}
          title="Les deux sous-agents ont répondu : la mise en regard est produite par le superviseur."
        >
          🔀 synthèse de deux sources
        </span>
      )}
    </span>
  );
}

function Trace({ seconds }: { seconds: number }) {
  const steps = [
    { label: 'Superviseur Foundry', detail: 'orchestration — ne recalcule rien', observed: true },
    { label: 'Appel A2A au sous-agent', detail: 'hop observé depuis le navigateur', observed: true },
    {
      label: 'Fabric — data agent, puis DAX ou GQL',
      detail: 'déclaré, non observé depuis l’application',
      observed: false,
    },
  ];

  return (
    <div className="glass rounded-xl p-4">
      <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
        Interrogation en cours — {seconds}s
      </div>
      <ol className="mt-3 space-y-2">
        {steps.map((s) => (
          <li key={s.label} className="flex items-start gap-2 text-xs">
            <span
              className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: s.observed ? 'var(--accent)' : 'var(--text-muted)' }}
            />
            <span>
              <span className="block font-medium" style={{ color: 'var(--text-primary)' }}>
                {s.label}
              </span>
              <span style={{ color: 'var(--text-muted)' }}>{s.detail}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-[11px]" style={{ color: 'var(--text-muted)' }}>
        Le routage se décide côté données : l’application ne recalcule aucun chiffre et ne choisit
        aucune source. Comptez 40 à 60 secondes.
      </p>
    </div>
  );
}

export function AgentPage() {
  const { key } = useParams();
  const persona = personaByKey(key);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  // Switching persona starts a new conversation: the four share one supervisor thread, and
  // carrying a Commerce answer into the Retention view would attribute it to the wrong framing.
  useEffect(() => {
    setTurns([]);
    setDraft('');
  }, [key]);

  useEffect(() => {
    if (!pending) return;
    setSeconds(0);
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [pending]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, pending]);

  const send = useCallback(
    async (question: string, expected: Source | null) => {
      const q = question.trim();
      // One question at a time: all four personas sit on the same supervisor, and two in flight
      // would let the answers swap places.
      if (!q || pending) return;

      const id = nextId.current++;
      setDraft('');
      setPending(true);
      setTurns((t) => [
        ...t,
        { id, question: q, expected, answer: null, toolsFired: [], error: null, seconds: 0 },
      ]);

      const started = Date.now();
      try {
        const res = await askSupervisor(q);
        const took = Math.round((Date.now() - started) / 1000);
        setTurns((t) =>
          t.map((x) =>
            x.id === id ? { ...x, answer: res.text, toolsFired: res.toolsFired, seconds: took } : x
          )
        );
      } catch (e) {
        const took = Math.round((Date.now() - started) / 1000);
        setTurns((t) =>
          t.map((x) =>
            x.id === id
              ? { ...x, error: e instanceof Error ? e.message : String(e), seconds: took }
              : x
          )
        );
      } finally {
        setPending(false);
      }
    },
    [pending]
  );

  if (!persona) return <Navigate to="/" replace />;

  return (
    <AppShell wide>
      <header className="flex items-center gap-4">
        <span
          className="flex h-14 w-14 items-center justify-center rounded-2xl text-2xl"
          style={{ background: `${persona.accent}1A` }}
        >
          {persona.icon}
        </span>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text-primary)' }}>
            {persona.name}
          </h1>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            {persona.description}
          </p>
        </div>
      </header>

      {!foundryConfigured && (
        <p className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          VITE_FOUNDRY_ENDPOINT n’est pas configuré : la conversation ne peut pas être envoyée.
        </p>
      )}

      {/* Data on the left, conversation on the right — one screen, so the audience sees that the
          figures and the agent are the same product rather than two features. */}
      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <section className="min-w-0">
          <PersonaPanels
            personaKey={persona.key}
            busy={pending}
            // A click on a figure always produces a `mixed` question: the chart already holds the
            // number, so what is left to ask is the *why* — which forces both subordinates to fire
            // and is the only moment the supervisor is visibly supervising.
            onAsk={(q) => void send(q, 'mixed')}
          />
        </section>

        <section className="flex min-w-0 flex-col lg:sticky lg:top-28 lg:max-h-[calc(100vh-9rem)]">
          <h2
            className="mb-3 text-xs font-semibold uppercase tracking-wide"
            style={{ color: 'var(--text-muted)' }}
          >
            Superviseur Foundry
          </h2>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
            <div className="glass rounded-2xl p-4">
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                {persona.welcome}
              </p>
            </div>

            {turns.length === 0 && (
              <div className="space-y-2">
                {persona.suggestions.map((s) => (
                  <button
                    key={s.q}
                    onClick={() => void send(s.q, s.expects)}
                    disabled={pending}
                    className="glass w-full rounded-xl p-3 text-left text-sm transition hover:-translate-y-0.5 disabled:opacity-50"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {s.q}
                    <span className="mt-2 flex">
                      <SourceChip source={s.expects} />
                    </span>
                  </button>
                ))}
                <p className="pt-1 text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                  Le badge annonce la source <em>attendue</em>. La route réellement empruntée
                  s’affiche sous la réponse — et peut différer.
                </p>
              </div>
            )}

            {turns.map((t) => (
              <div key={t.id} className="space-y-3">
                <div className="flex justify-end">
                  <div
                    className="max-w-[85%] rounded-2xl px-4 py-2.5 text-sm text-white"
                    style={{ background: persona.accent }}
                  >
                    {t.question}
                    {t.expected && (
                      <span className="mt-2 flex">
                        <span className="rounded-full bg-white/20 px-2 py-0.5 text-[11px]">
                          attendu : {SOURCE_STYLE[t.expected].label}
                        </span>
                      </span>
                    )}
                  </div>
                </div>

                {t.answer !== null && (
                  <div className="glass rounded-2xl p-4" style={{ color: 'var(--text-primary)' }}>
                    <Markdown text={t.answer} />
                    <div
                      className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3"
                      style={{ borderColor: 'var(--border)' }}
                    >
                      <RoutingBadges tools={t.toolsFired} />
                      <span className="ml-auto text-[11px]" style={{ color: 'var(--text-muted)' }}>
                        {t.seconds}s
                      </span>
                    </div>
                  </div>
                )}

                {t.error && (
                  <div className="rounded-2xl border border-red-300 bg-red-50 p-4 text-sm text-red-800">
                    <p className="font-medium">La question n’a pas abouti ({t.seconds}s).</p>
                    <p className="mt-1 break-words font-mono text-xs">{t.error}</p>
                  </div>
                )}
              </div>
            ))}

            {pending && <Trace seconds={seconds} />}
            <div ref={bottom} />
          </div>

          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send(draft, null);
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={pending}
              placeholder="Nommez la table et la colonne…"
              className="glass min-w-0 flex-1 rounded-full px-5 py-3 text-sm outline-none disabled:opacity-60"
              style={{ color: 'var(--text-primary)' }}
            />
            <button
              type="submit"
              disabled={pending || !draft.trim()}
              className="rounded-full px-5 py-3 text-sm font-medium text-white disabled:opacity-40"
              style={{ background: persona.accent }}
            >
              {pending ? '…' : 'Envoyer'}
            </button>
          </form>
        </section>
      </div>
    </AppShell>
  );
}
