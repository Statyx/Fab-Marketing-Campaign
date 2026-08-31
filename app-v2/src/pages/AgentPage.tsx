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
import { personaByKey, pickVaried, type Source } from '@/data/personas';
import { splitAnswer } from '@/services/answer';
import { askSupervisor, foundryConfigured, SupervisorError } from '@/services/foundry';

interface Turn {
  id: number;
  question: string;
  expected: Source | null;
  answer: string | null;
  toolsFired: string[];
  error: string | null;
  detail: string | null;
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
    label: 'Chiffres',
    icon: '📊',
    tint: 'rgba(124,92,230,0.12)',
    ink: 'var(--accent)',
  },
  ontology: { label: 'Graphe de relations', icon: '🕸', tint: 'rgba(2,113,128,0.12)', ink: '#027180' },
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

/**
 * The connection names the supervisor reports, in the words of the room.
 *
 * `FrontDoorA2A` and `VoiceOfCustomerA2A` are the identifiers of the two subordinate agents, and
 * they were printed raw, in mono, under every answer. An unknown name still falls through
 * verbatim: if a third subordinate is ever wired in, it must be *visible*, not silently relabelled
 * into one of these two — the whole point of this badge is that it reports what happened.
 */
const TOOL_LABEL: Record<string, string> = {
  FrontDoorA2A: 'Données Fabric',
  VoiceOfCustomerA2A: 'Verbatims clients',
};

/** What the supervisor actually did, read off the response items. */
function RoutingBadges({ tools }: { tools: string[] }) {
  if (tools.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
        ⚠ réponse non sourcée — aucune donnée n’a été consultée
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
        Source consultée :
      </span>
      {tools.map((t, i) => (
        <span
          key={`${t}-${i}`}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
        >
          {TOOL_LABEL[t] ?? t}
        </span>
      ))}
      {bothConsulted && (
        <span
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold"
          style={{ background: 'rgba(134,60,65,0.12)', color: '#863C41' }}
          title="Les deux sources ont répondu : la mise en regard est produite par l’assistant."
        >
          🔀 synthèse de deux sources
        </span>
      )}
    </span>
  );
}

/**
 * One answer bubble: the prose, then the provenance behind a button.
 *
 * The provenance is not decoration and is not being buried — it is the difference between a
 * sourced figure and a chatbot that sounds sure of itself, and this app's whole argument rests
 * on it. But printed inline it made the answer unreadable: identifiers in the lead sentence,
 * then the same fields again as a bulleted form. It is one click away, closed by default,
 * because a marketing lead reads the sentence and an architect asks for the query.
 *
 * The toggle is per turn, so opening the detail on one answer does not open it on every answer
 * in the thread — a shared flag would turn one click into a wall of identifiers.
 */
function Answer({ text, tools, seconds }: { text: string; tools: string[]; seconds: number }) {
  const [open, setOpen] = useState(false);
  const { body, source } = splitAnswer(text);

  return (
    <div
      className="glass rounded-2xl p-3.5 text-[0.8125rem] leading-relaxed"
      style={{ color: 'var(--text-primary)' }}
    >
      <Markdown text={body} />

      {source && (
        <div className="mt-2.5">
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] transition hover:opacity-80"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
          >
            <span aria-hidden>{open ? '▾' : '▸'}</span>
            {open ? 'Masquer le détail technique' : 'Source et requête'}
          </button>
          {open && (
            <div
              className="mt-2 overflow-x-auto rounded-xl px-3 py-2 font-mono text-[11px] leading-relaxed"
              style={{ background: 'var(--surface-2, rgba(127,127,127,0.08))', color: 'var(--text-secondary)' }}
            >
              {source.split('\n').map((line, i) => (
                <div key={i} className="whitespace-pre-wrap">
                  {line}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className="mt-2.5 flex flex-wrap items-center gap-2 border-t pt-2.5"
        style={{ borderColor: 'var(--border)' }}
      >
        <RoutingBadges tools={tools} />
        <span className="ml-auto text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {seconds}s
        </span>
      </div>
    </div>
  );
}

/**
 * A failed turn, in the same two registers as an answer: a sentence, and the payload folded
 * underneath. The app used to print the raw `Foundry 400: {...}` — id, error type and a
 * Microsoft troubleshooting URL — straight onto the stage.
 */
function Failure({ message, detail, seconds }: { message: string; detail: string | null; seconds: number }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-2xl border border-red-300 bg-red-50 p-3.5 text-[0.8125rem] text-red-900 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200">
      <p className="font-medium">{message}</p>
      <p className="mt-1 text-[11px] opacity-70">Abandonné après {seconds}s.</p>

      {detail && (
        <div className="mt-2.5">
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 rounded-full bg-red-100 px-2.5 py-1 text-[11px] text-red-800 transition hover:opacity-80 dark:bg-red-900/40 dark:text-red-200"
          >
            <span aria-hidden>{open ? '▾' : '▸'}</span>
            {open ? 'Masquer le détail technique' : 'Détail technique'}
          </button>
          {open && (
            <div className="mt-2 overflow-x-auto rounded-xl bg-red-100/70 px-3 py-2 font-mono text-[11px] leading-relaxed break-words dark:bg-red-900/30">
              {detail}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Trace({ seconds, attempt }: { seconds: number; attempt: number }) {
  // Plain French, and the third step stays honest. Only the assistant's own hops are observable
  // from the browser; what happens inside Fabric arrives with the result and is never watched
  // live. The muted dot and "non mesuré ici" carry that — dropping the jargon must not turn a
  // declared step into an animated one.
  const steps = [
    { label: 'L’assistant analyse la question', detail: 'il choisit les sources à interroger', observed: true },
    { label: 'Interrogation des sources', detail: 'échange observé depuis l’application', observed: true },
    {
      label: 'Calcul côté Fabric',
      detail: 'annoncé par l’assistant, non mesuré ici',
      observed: false,
    },
  ];

  return (
    <div className="glass rounded-xl p-4">
      <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
        Interrogation en cours — {seconds}s
      </div>
      {attempt > 1 && (
        <p className="mt-1.5 text-xs" style={{ color: 'var(--accent)' }}>
          La liaison s’est interrompue — reprise automatique (tentative {attempt}).
        </p>
      )}
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
        Comptez une à deux minutes selon la question.
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
  const [attempt, setAttempt] = useState(1);
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
    // `label` is what the conversation shows; `question` is what the agent receives. They differ
    // when a chart click builds a prompt that names the table and the column — precision the
    // answer depends on, jargon the room should never read. Free-typed questions pass through
    // unchanged, so `label` defaults to the question itself.
    async (question: string, expected: Source | null, label?: string) => {
      const q = question.trim();
      // One question at a time: all four personas sit on the same supervisor, and two in flight
      // would let the answers swap places.
      if (!q || pending) return;

      const id = nextId.current++;
      setDraft('');
      setPending(true);
      setAttempt(1);
      setTurns((t) => [
        ...t,
        {
          id,
          question: label ?? q,
          expected,
          answer: null,
          toolsFired: [],
          error: null,
          detail: null,
          seconds: 0,
        },
      ]);

      const started = Date.now();
      try {
        const res = await askSupervisor(q, { onAttempt: setAttempt });
        const took = Math.round((Date.now() - started) / 1000);
        setTurns((t) =>
          t.map((x) =>
            x.id === id ? { ...x, answer: res.text, toolsFired: res.toolsFired, seconds: took } : x
          )
        );
      } catch (e) {
        const took = Math.round((Date.now() - started) / 1000);
        const message =
          e instanceof SupervisorError
            ? e.message
            : 'L’assistant n’a pas pu traiter cette question.';
        const detail =
          e instanceof SupervisorError ? e.detail : e instanceof Error ? e.message : String(e);
        setTurns((t) =>
          t.map((x) => (x.id === id ? { ...x, error: message, detail, seconds: took } : x))
        );
      } finally {
        setPending(false);
      }
    },
    [pending]
  );

  if (!persona) return <Navigate to="/" replace />;

  // Suggestions in two acts.
  //
  // Act one showed all eight at once — eight cards, eight badges, before anyone had asked
  // anything. "On voit trop de trucs à cliquer": a wall of options is not an invitation, it is a
  // decision to make, and the demo stalls on it. Three is enough to show the *kinds* of question
  // this assistant takes.
  //
  // Act two showed nothing at all, which was worse: the first answer emptied the rail and left a
  // blank box asking for free text — right when the audience has just learned what good questions
  // look like and wants to keep going. So the remaining suggestions stay, as compact chips above
  // the composer, minus the ones already asked (re-offering a question just answered reads as the
  // app not following its own conversation).
  //
  // Act three is the one that cost a capability. Cutting to three was done with `slice(0, 3)`,
  // and the registry happens to list the numeric questions first and the graph ones last — so
  // *every* ontology question in the product became unreachable in one edit, silently, and the
  // graph simply stopped being demonstrable. Nothing failed; the openers just quietly became
  // three variations of the same question. Pick one per family instead of the first three: a
  // figure, then the graph, then the cross-source question only the supervisor can answer. When
  // a cap and an ordering meet, the cap decides what the product appears to do.
  const asked = new Set(turns.map((t) => t.question));
  const unasked = persona.suggestions.filter((s) => !asked.has(s.q));
  const starters = pickVaried(unasked, 3);
  const followUps = pickVaried(unasked, 3);

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
          L’assistant n’est pas configuré : la conversation ne peut pas être envoyée.
          {/* The variable name stays reachable for whoever has to fix it, off the stage. */}
          <span className="sr-only"> (VITE_FOUNDRY_ENDPOINT absent)</span>
        </p>
      )}

      {/* Data on the left, conversation on the right — one screen, so the audience sees that the
          figures and the agent are the same product rather than two features.

          The right column is a *fixed rail*, not a fraction. A `1fr` split gave the conversation
          half the screen, which read as "this is a chat app that happens to show charts" — the
          exact opposite of the point. Pinning it at 22-24rem is the Copilot staging: the assistant
          is a companion at the edge, the workspace keeps doing its job on its own. It also lets
          the panels go to four KPI columns, so the data is wide instead of stacked. */}
      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_24rem]">
        <section className="min-w-0">
          <PersonaPanels
            personaKey={persona.key}
            busy={pending}
            // A click on a figure always produces a `mixed` question: the chart already holds the
            // number, so what is left to ask is the *why* — which forces both subordinates to fire
            // and is the only moment the supervisor is visibly supervising.
            onAsk={(prompt, label) => void send(prompt, 'mixed', label)}
          />
        </section>

        <section className="flex min-w-0 flex-col lg:sticky lg:top-28 lg:max-h-[calc(100vh-9rem)]">
          <h2
            className="mb-3 text-xs font-semibold uppercase tracking-wide"
            style={{ color: 'var(--text-muted)' }}
          >
            Demandez à l’assistant
          </h2>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            <div className="glass rounded-2xl p-3.5">
              <p className="text-[0.8125rem] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {persona.welcome}
              </p>
            </div>

            {turns.length === 0 && (
              <div className="space-y-2">
                {starters.map((s) => (
                  <button
                    key={s.q}
                    onClick={() => void send(s.q, s.expects)}
                    disabled={pending}
                    className="glass w-full rounded-xl p-3 text-left text-[0.8125rem] leading-snug transition hover:-translate-y-0.5 disabled:opacity-50"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {s.q}
                    <span className="mt-1.5 flex">
                      <SourceChip source={s.expects} />
                    </span>
                  </button>
                ))}
                <p className="pt-1 text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                  L’assistant choisit lui-même où chercher. La source réellement consultée
                  s’affiche sous la réponse.
                </p>
              </div>
            )}

            {turns.map((t) => (
              <div key={t.id} className="space-y-3">
                <div className="flex justify-end">
                  <div
                    className="max-w-[88%] rounded-2xl px-3.5 py-2 text-[0.8125rem] leading-snug text-white"
                    style={{ background: persona.accent }}
                  >
                    {t.question}
                    {t.expected && (
                      <span className="mt-1.5 flex">
                        <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px]">
                          attendu : {SOURCE_STYLE[t.expected].label}
                        </span>
                      </span>
                    )}
                  </div>
                </div>

                {t.answer !== null && (
                  <Answer text={t.answer} tools={t.toolsFired} seconds={t.seconds} />
                )}

                {t.error && <Failure message={t.error} detail={t.detail} seconds={t.seconds} />}
              </div>
            ))}

            {pending && <Trace seconds={seconds} attempt={attempt} />}
            <div ref={bottom} />
          </div>

          {turns.length > 0 && followUps.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {followUps.map((s) => (
                <button
                  key={s.q}
                  onClick={() => void send(s.q, s.expects)}
                  disabled={pending}
                  title={s.q}
                  className="glass max-w-full truncate rounded-full px-3 py-1.5 text-[11px] transition hover:-translate-y-0.5 disabled:opacity-40"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {s.q}
                </button>
              ))}
            </div>
          )}

          <form
            className="mt-2.5 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send(draft, null);
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={pending}
              placeholder="Posez votre question…"
              className="glass min-w-0 flex-1 rounded-full px-4 py-2.5 text-[0.8125rem] outline-none disabled:opacity-60"
              style={{ color: 'var(--text-primary)' }}
            />
            <button
              type="submit"
              disabled={pending || !draft.trim()}
              className="rounded-full px-4 py-2.5 text-[0.8125rem] font-medium text-white disabled:opacity-40"
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
