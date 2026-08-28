/**
 * Connectivity proof for the V2 app.
 *
 * This page exists because everything upstream of it could be verified from a terminal —
 * capacity, workspace, deployment, CORS preflights, admin consent — except the one thing that
 * matters: whether a *custom* Entra SPA registration can actually obtain and use delegated
 * tokens for Fabric and Foundry from a browser. That can only be observed in a browser.
 *
 * It is deliberately routed OUTSIDE the auth guard: a diagnostic that redirects away when
 * sign-in fails is a diagnostic that hides the failure it was written to surface.
 *
 * Each step reports what was actually returned (audience, scopes, item count, which A2A tool
 * fired), not merely that a call did not throw.
 */
import { useState } from 'react';

import { listItems, dataWorkspaceId } from '@/services/fabric';
import { askSupervisor, foundryConfigured } from '@/services/foundry';
import {
  decodeJwt,
  FABRIC_SCOPES,
  FOUNDRY_SCOPES,
  getToken,
  msalConfigured,
} from '@/services/msal';
import { MsalAuthService } from '@/services/MsalAuthService';
import { DAX_AT_RISK, executeDax, powerbiConfigured, semanticModelId } from '@/services/powerbi';

type State = 'idle' | 'running' | 'pass' | 'fail';

interface Step {
  key: string;
  label: string;
  state: State;
  detail?: string;
}

const STEPS: Step[] = [
  { key: 'signin', label: '1. Connexion Entra (MSAL, popup)', state: 'idle' },
  { key: 'tok-fabric', label: '2. Jeton pour api.fabric.microsoft.com', state: 'idle' },
  { key: 'fabric', label: '3. Lecture réelle du workspace de données (Fabric REST)', state: 'idle' },
  { key: 'tok-foundry', label: '4. Jeton pour ai.azure.com', state: 'idle' },
  { key: 'foundry', label: '5. Question au superviseur Foundry (lent : ~40-60 s)', state: 'idle' },
  { key: 'dax', label: '6. Même chiffre en DAX sur le modèle sémantique (rapide)', state: 'idle' },
];

const QUESTION =
  'Combien de clients sont à risque de churn ? ' +
  "Utilise la colonne risk_band de la table customer_profile (bandes High et Critical).";

function badge(s: State) {
  const map: Record<State, string> = {
    idle: 'bg-gray-100 text-gray-500',
    running: 'bg-blue-100 text-blue-700',
    pass: 'bg-green-100 text-green-700',
    fail: 'bg-red-100 text-red-700',
  };
  const label: Record<State, string> = {
    idle: 'en attente',
    running: 'en cours…',
    pass: 'OK',
    fail: 'ÉCHEC',
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[s]}`}>{label[s]}</span>;
}

/** Audience + granted scopes, so a token that came back can be told from the *right* token. */
function describeToken(token: string): string {
  const c = decodeJwt(token) as Record<string, string> | null;
  if (!c) return 'jeton reçu (payload illisible)';
  const scopes = (c.scp ?? '').split(' ').filter(Boolean);
  return `aud=${c.aud}\nupn=${c.upn ?? c.preferred_username ?? '?'}\nscp=${
    scopes.length ? scopes.join(', ') : '(aucun)'
  }`;
}

export function DiagnosticsPage() {
  const [steps, setSteps] = useState<Step[]>(STEPS);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<string>('');

  const set = (key: string, state: State, detail?: string) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, state, detail } : s)));

  async function run() {
    setBusy(true);
    setAnswer('');
    setSteps(STEPS.map((s) => ({ ...s, state: 'idle', detail: undefined })));

    // Each step is awaited and reported separately: a single try/catch around the whole
    // sequence would tell us it broke without telling us where.
    try {
      set('signin', 'running');
      const auth = new MsalAuthService();
      const user = (await auth.getCurrentUser()) ?? (await auth.signIn());
      set('signin', 'pass', `${user.name} <${user.email}>`);
    } catch (e) {
      set('signin', 'fail', String(e));
      setBusy(false);
      return;
    }

    let fabricOk = false;
    try {
      set('tok-fabric', 'running');
      const t = await getToken(FABRIC_SCOPES);
      set('tok-fabric', 'pass', describeToken(t));
      fabricOk = true;
    } catch (e) {
      set('tok-fabric', 'fail', String(e));
    }

    if (fabricOk) {
      try {
        set('fabric', 'running');
        const items = await listItems();
        const byType = items.reduce<Record<string, number>>((acc, i) => {
          acc[i.type] = (acc[i.type] ?? 0) + 1;
          return acc;
        }, {});
        set(
          'fabric',
          'pass',
          `${items.length} éléments lus dans ${dataWorkspaceId}\n` +
            Object.entries(byType)
              .map(([t, n]) => `  ${t}: ${n}`)
              .join('\n')
        );
      } catch (e) {
        set('fabric', 'fail', String(e));
      }
    } else {
      set('fabric', 'fail', 'ignoré : pas de jeton Fabric');
    }

    let foundryOk = false;
    let supervisorText = '';
    try {
      set('tok-foundry', 'running');
      const t = await getToken(FOUNDRY_SCOPES);
      set('tok-foundry', 'pass', describeToken(t));
      foundryOk = true;
    } catch (e) {
      set('tok-foundry', 'fail', String(e));
    }

    if (foundryOk && foundryConfigured) {
      try {
        set('foundry', 'running');
        const t0 = Date.now();
        const r = await askSupervisor(QUESTION);
        const secs = ((Date.now() - t0) / 1000).toFixed(1);
        supervisorText = r.text;
        setAnswer(r.text);
        set(
          'foundry',
          r.toolsFired.length ? 'pass' : 'fail',
          `${secs}s — outils A2A déclenchés : ${
            r.toolsFired.length ? r.toolsFired.join(', ') : 'AUCUN (réponse non sourcée)'
          }`
        );
      } catch (e) {
        set('foundry', 'fail', String(e));
      }
    } else {
      set('foundry', 'fail', foundryConfigured ? 'ignoré : pas de jeton' : 'VITE_FOUNDRY_ENDPOINT absent');
    }

    if (powerbiConfigured) {
      try {
        set('dax', 'running');
        const t0 = Date.now();
        const [row] = await executeDax(DAX_AT_RISK);
        const secs = ((Date.now() - t0) / 1000).toFixed(1);
        if (!row) throw new Error('La requête DAX n’a retourné aucune ligne.');

        // Measure and raw column are asked in the same query so a broken column reference
        // cannot hide behind a working measure.
        const viaMeasure = row['[Measure]'];
        const viaColumn = row['[Column]'];
        const internallyConsistent = String(viaMeasure) === String(viaColumn);

        // A soft cross-check against the supervisor, deliberately not a pass/fail assertion: it
        // answers in prose, so "the number is absent from the text" can mean it disagreed OR
        // that it phrased things differently. A verdict here would outrun the evidence.
        const verdict = !supervisorText
          ? '(pas de réponse du superviseur à comparer)'
          : supervisorText.includes(String(viaMeasure))
            ? '✓ concorde avec la réponse du superviseur'
            : '≠ chiffre absent du texte du superviseur — à vérifier à la main';

        set(
          'dax',
          internallyConsistent ? 'pass' : 'fail',
          `mesure [Customers at Risk] = ${viaMeasure}\n` +
            `colonne risk_band ∈ {High, Critical} = ${viaColumn}\n` +
            `${internallyConsistent ? '✓ mesure et colonne concordent' : '✗ DIVERGENCE mesure/colonne'} — ${secs}s\n` +
            `modèle ${semanticModelId}\n${verdict}`
        );
      } catch (e) {
        set('dax', 'fail', String(e));
      }
    } else {
      set('dax', 'fail', 'VITE_SEMANTIC_MODEL_ID absent du build');
    }

    setBusy(false);
  }

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-2xl font-semibold text-gray-900">Customer 360 Cockpit — contrôle de connectivité</h1>
        <p className="mt-2 text-sm text-gray-600">
          Vérifie la chaîne complète : authentification, jeton Fabric, puis évaluation DAX sur le
          modèle sémantique. Lecture seule, aucune écriture.
        </p>

        {!msalConfigured && (
          <div className="mt-4 p-3 rounded bg-red-50 text-red-700 text-sm">
            VITE_ENTRA_CLIENT_ID / VITE_ENTRA_TENANT_ID absents du build.
          </div>
        )}

        <button
          onClick={run}
          disabled={busy || !msalConfigured}
          className="mt-6 px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium disabled:opacity-50"
        >
          {busy ? 'Test en cours…' : 'Lancer le test'}
        </button>

        <ul className="mt-6 space-y-3">
          {steps.map((s) => (
            <li key={s.key} className="bg-white rounded border border-gray-200 p-4">
              <div className="flex items-center justify-between gap-4">
                <span className="text-sm text-gray-800">{s.label}</span>
                {badge(s.state)}
              </div>
              {s.detail && (
                <pre className="mt-2 text-xs text-gray-600 whitespace-pre-wrap break-all">{s.detail}</pre>
              )}
            </li>
          ))}
        </ul>

        {answer && (
          <div className="mt-6 bg-white rounded border border-gray-200 p-4">
            <h2 className="text-sm font-semibold text-gray-900">Réponse du superviseur</h2>
            <p className="mt-2 text-sm text-gray-700 whitespace-pre-wrap">{answer}</p>
          </div>
        )}
      </div>
    </div>
  );
}
