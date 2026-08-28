/**
 * Step 4 — Act: ask the Foundry supervisor.
 *
 * Three things this screen must not do, each learned the hard way:
 *
 * 1. **Never present an unsourced answer as an answer.** Both subordinates surface as
 *    `a2a_preview_call`, so the item type no longer says which one ran — only the connection
 *    name does. If no A2A tool fired, the model answered from itself and the reply is flagged
 *    rather than shown as sourced.
 * 2. **Never retry an empty retrieval.** The corpus agent legitimately returns nothing about
 *    once in nine; asking again until documents appear manufactures confidence that was not
 *    earned.
 * 3. **Never round or restate the figures.** The supervisor is instructed to relay them
 *    verbatim with their scope ("825 customers, risk_band High + Critical"); the UI shows the
 *    text as returned and adds nothing to it.
 *
 * The suggested questions all name a table *and* a column on purpose: "how many customers are
 * at risk" has three correct answers in this model (800 / 825 / 593), so an unscoped question
 * is under-specified rather than unstable, and re-asking it is not a fix.
 */
import { useEffect, useRef, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { askSupervisor, foundryConfigured } from '@/services/foundry';

interface Turn {
  question: string;
  answer?: string;
  toolsFired?: string[];
  seconds?: number;
  error?: string;
}

const SUGGESTED = [
  "Combien de clients sont à risque de churn ? Utilise la colonne risk_band de la table crm_customer_profile (bandes High et Critical).",
  "Quelle campagne de la table marketing_campaigns a le plus d'envois par client, et quel est son taux de désabonnement dans marketing_events ?",
  "Quel segment de crm_segments concentre le plus de CLV à risque ?",
  "Que disent les clients mécontents dans le corpus de verbatims ?",
];

/** A live counter, because a 40-60 s wait with a static spinner reads as a hung request. */
function Elapsed() {
  const [s, setS] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setS((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="tabular-nums">
      {s} s — le routage se fait côté données, comptez 40 à 60 s
    </span>
  );
}

export function ActPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;

    setInput('');
    setBusy(true);
    setTurns((prev) => [...prev, { question: q }]);

    const t0 = Date.now();
    try {
      const r = await askSupervisor(q);
      const seconds = Math.round((Date.now() - t0) / 1000);
      setTurns((prev) =>
        prev.map((t, i) =>
          i === prev.length - 1 ? { ...t, answer: r.text, toolsFired: r.toolsFired, seconds } : t
        )
      );
    } catch (e) {
      const seconds = Math.round((Date.now() - t0) / 1000);
      setTurns((prev) =>
        prev.map((t, i) =>
          i === prev.length - 1
            ? { ...t, error: String(e instanceof Error ? e.message : e), seconds }
            : t
        )
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell
      title="4. Agir"
      intro="Le superviseur Foundry orchestre deux subordonnés : les chiffres viennent de l'agent de données Fabric, les verbatims du corpus documentaire. Aucun calcul n'est refait côté Foundry."
    >
      {!foundryConfigured && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          VITE_FOUNDRY_ENDPOINT absent du build : le superviseur n'est pas joignable.
        </div>
      )}

      {turns.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <p className="text-sm font-medium text-slate-900">Questions suggérées</p>
          <p className="mt-1 text-xs text-slate-500">
            Chacune nomme explicitement la table et la colonne : sans cela, « à risque » a trois
            réponses également correctes dans ce modèle.
          </p>
          <div className="mt-4 space-y-2">
            {SUGGESTED.map((q) => (
              <button
                key={q}
                onClick={() => void ask(q)}
                disabled={busy || !foundryConfigured}
                className="block w-full rounded-lg border border-slate-200 px-4 py-3 text-left text-sm text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-6 space-y-4">
        {turns.map((t, i) => (
          <div key={i} className="space-y-3">
            <div className="ml-auto max-w-2xl rounded-xl bg-blue-600 px-4 py-3 text-sm text-white">
              {t.question}
            </div>

            {t.answer !== undefined && (
              <div className="max-w-3xl rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="whitespace-pre-wrap text-sm text-slate-800">{t.answer}</p>
                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                  {t.toolsFired && t.toolsFired.length > 0 ? (
                    t.toolsFired.map((name) => (
                      <span
                        key={name}
                        className="rounded bg-emerald-50 px-2 py-0.5 font-mono text-[11px] text-emerald-700"
                      >
                        A2A → {name}
                      </span>
                    ))
                  ) : (
                    <span className="rounded bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700">
                      Aucun outil A2A déclenché — réponse non sourcée
                    </span>
                  )}
                  <span className="text-[11px] text-slate-400">{t.seconds} s</span>
                </div>
              </div>
            )}

            {t.error && (
              <div className="max-w-3xl rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="text-sm font-medium text-red-800">Appel en échec ({t.seconds} s)</p>
                <pre className="mt-2 whitespace-pre-wrap break-all text-xs text-red-700">
                  {t.error}
                </pre>
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500 shadow-sm">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
            <Elapsed />
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
        className="mt-6 flex gap-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy || !foundryConfigured}
          placeholder="Nommez la table et la colonne pour une réponse reproductible…"
          className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-blue-500 disabled:bg-slate-100"
        />
        <button
          type="submit"
          disabled={busy || !input.trim() || !foundryConfigured}
          className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
        >
          Demander
        </button>
      </form>
    </AppShell>
  );
}
