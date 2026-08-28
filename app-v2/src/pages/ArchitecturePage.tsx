/**
 * Architecture — the deployed chain and the ontology, both derived and both checked.
 *
 * V1 had these as two modals fed by a FastAPI backend that re-read `state.json` on every open.
 * V2 has no backend, so the shape is baked at build time from the same tested Python builder and
 * the *status* is asked of the tenant here, live. That is a stronger claim than V1's, which
 * could only report what a JSON file on the deploy machine remembered.
 *
 * The counter says "observés dans le tenant", not "déployés". The distinction is the whole
 * point: this page reports what the Fabric and Foundry services answered a few seconds ago, and
 * a box it could not ask about says so rather than being quietly drawn as healthy.
 */
import { useCallback, useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { GraphDiagram } from '@/components/GraphDiagram';
import { WorkflowDiagram } from '@/components/WorkflowDiagram';
import {
  ontology,
  verifyTopology,
  workflow,
  type Verification,
} from '@/services/topology';

const LEGEND = [
  { cls: 'is-foundry', text: 'Foundry — orchestration' },
  { cls: 'is-fabric', text: 'Fabric — agent de données, lakehouse' },
  { cls: 'is-semantic', text: 'Modèle sémantique (DAX)' },
  { cls: 'is-ontology', text: 'Ontologie (GQL)' },
  { cls: 'is-absent', text: 'absent du tenant' },
  { cls: 'is-unchecked', text: 'non vérifié' },
];

function Card({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <section
      className="rounded-2xl border p-6"
      style={{ background: 'var(--bg-card-solid)', borderColor: 'var(--border)' }}
    >
      <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h2>
      <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
        {sub}
      </p>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function List({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-[260px] flex-1">
      <p className="text-xs font-semibold uppercase tracking-wide"
         style={{ color: 'var(--text-muted)' }}>
        {title}
      </p>
      <ul className="mt-2 space-y-1.5 text-xs leading-relaxed"
          style={{ color: 'var(--text-secondary)' }}>
        {children}
      </ul>
    </div>
  );
}

export function ArchitecturePage() {
  const [check, setCheck] = useState<Verification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const run = useCallback(() => {
    setBusy(true);
    setError(null);
    verifyTopology()
      .then(setCheck)
      .catch((e: unknown) => setError(String((e as Error)?.message ?? e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(run, [run]);

  const checks = check?.checks ?? {};
  const observed = workflow.nodes.filter((n) => checks[n.id]?.status === 'live').length;
  const byId = new Map(workflow.nodes.map((n) => [n.id, n]));

  return (
    <AppShell
      wide
      title="Architecture"
      intro="La topologie est dérivée du code de déploiement, jamais dessinée à la main. L’état de
             chaque composant est demandé à Fabric et à Foundry à l’ouverture de cette page — un
             fichier local ne peut pas prouver qu’un élément existe encore."
    >
      <div className="space-y-8">
        <Card
          title="Chaîne d’agents déployée"
          sub={
            busy
              ? 'Interrogation de Fabric et de Foundry…'
              : `${observed}/${workflow.nodes.length} composants observés dans le tenant · ` +
                `${workflow.edges.length} sauts · ${workflow.crossings} croisement`
          }
        >
          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4">
              <p className="text-sm font-medium text-red-800">La vérification a échoué</p>
              <pre className="mt-2 whitespace-pre-wrap break-all text-xs text-red-700">{error}</pre>
              <button
                onClick={run}
                className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
              >
                Réessayer
              </button>
            </div>
          )}

          <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
            {LEGEND.map((l) => (
              <span key={l.cls} className={`wf-key ${l.cls}`}>
                {l.text}
              </span>
            ))}
          </div>

          <WorkflowDiagram nodes={workflow.nodes} edges={workflow.edges} checks={checks} />

          {check && check.warnings.length > 0 && (
            <div
              className="mt-5 rounded-lg border p-4 text-xs leading-relaxed"
              style={{
                borderColor: 'var(--warn-border, #fcd34d)',
                background: 'var(--warn-bg, #fffbeb)',
                color: 'var(--warn-text, #78350f)',
              }}
            >
              <b>⚠ Écarts constatés</b>
              {check.warnings.map((w, i) => (
                <div key={i} className="mt-1">
                  {w}
                </div>
              ))}
            </div>
          )}

          <div className="mt-6 flex flex-wrap gap-6">
            <List title="Sauts">
              {workflow.edges.map((e) => (
                <li key={`${e.from}-${e.to}`}>
                  <b>{byId.get(e.from)?.label ?? e.from}</b>
                  {' —'}
                  <em>{e.protocol}</em>
                  {'→ '}
                  <b>{byId.get(e.to)?.label ?? e.to}</b>
                  {e.label && <span style={{ color: 'var(--text-muted)' }}> via {e.label}</span>}
                  {e.note && <span style={{ color: 'var(--text-muted)' }}> · {e.note}</span>}
                </li>
              ))}
            </List>
            <List title="Composants">
              {workflow.nodes.map((n) => {
                const c = checks[n.id];
                return (
                  <li key={n.id}>
                    <b>{n.label}</b>{' '}
                    <span style={{ color: 'var(--text-muted)' }}>
                      ·{' '}
                      {c?.status === 'live'
                        ? `observé${c.observed ? ` — ${c.observed}` : ''}`
                        : (c?.note ?? 'non vérifié')}
                    </span>
                    <br />
                    <span style={{ color: 'var(--text-muted)', opacity: 0.85 }}>{n.contract}</span>
                  </li>
                );
              })}
            </List>
          </div>
        </Card>

        <Card
          title="Ontologie"
          sub={`${ontology.entities.length} entités · ${ontology.relationships.length} relations · interrogeable en GQL`}
        >
          <GraphDiagram
            entities={ontology.entities}
            relationships={ontology.relationships}
          />
          <div className="mt-6 flex flex-wrap gap-6">
            <List title="Entités">
              {ontology.entities.map((e) => (
                <li key={e.name}>
                  <b>{e.name}</b>{' '}
                  <span style={{ color: 'var(--text-muted)' }}>
                    ← {e.table} · {e.properties} propriétés
                  </span>
                </li>
              ))}
            </List>
            <List title="Relations">
              {ontology.relationships.map((r, i) => (
                <li key={`${r.name}-${i}`}>
                  <b>{r.from}</b>
                  {' —'}
                  <em>{r.name}</em>
                  {'→ '}
                  <b>{r.to}</b>
                </li>
              ))}
            </List>
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
