/**
 * The visuals that belong to each persona — the half of the app that used to be filed under
 * "Parcours guidé" and was therefore invisible.
 *
 * ── Why these panels exist here rather than on four separate screens ─────────────────────
 * The app carried two navigations for one subject: four personas (chat, no data) and four
 * arc steps (all the charts, no chat). They were the same four topics under two taxonomies —
 * the persona descriptions literally used the arc vocabulary ("Détection", "Diagnostic",
 * "Impact business"). Filing the charts under a *method* name meant nothing on screen told
 * the audience that live DAX ran behind "Détecter", so the app read as a chatbot.
 *
 * ── The rule that shapes every panel ────────────────────────────────────────────────────
 * **A chart is where a question is born.** Every row here is a button: clicking it sends a
 * question to the supervisor with the figure already on screen. The chart supplies the
 * *combien*, the agent supplies the *pourquoi*. That is also the only staging in which the
 * supervisor visibly supervises — the click produces a `mixed` question, so both subordinates
 * have to fire and the routing badges show two distinct connection names.
 *
 * ── Invariants carried over from the deleted screens — do not lose these ─────────────────
 *  1. **`At Risk %` is a share of *buyers*, not of all customers.** Recomputing it in the
 *     browser gives 6.9 % where the model says 7.8 %, and the app then contradicts the Power BI
 *     report. It is a model measure; it is read, never derived.
 *  2. **The campaign panel must never name the culprit.** The outlier is found arithmetically
 *     (median × factor) precisely so the cause *emerges* from the data during the demo.
 *     `personas.ts` may name it in a scripted question; this panel may not.
 *  3. **Segments overlap on purpose**, so per-segment CLV does not sum to the total. The share
 *     is a ratio to the model's unfiltered [CLV at Risk]. The caption saying so travels with
 *     the table — without it the arithmetic looks broken.
 *  4. **Prospects are 0 by construction**: no order means no churn to measure, only a
 *     conversion problem.
 *
 * ── Why every generated question names a table *and* a column ───────────────────────────
 * "How many customers are at risk" has three equally correct answers in this model
 * (risk_band='High' → 800, churn_risk_score ≥ 65 → 825, lifecycle_stage='at_risk' → 593).
 * An unscoped question is under-specified, not unstable, and re-asking it is not a fix.
 */
import type { ReactElement } from 'react';

import { KpiCard } from '@/components/KpiCard';
import { QueryState } from '@/components/QueryState';
import { useDax } from '@/hooks/useDax';
import { fmtEur, fmtInt, fmtPct } from '@/lib/format';
import {
  CAMPAIGN_PRESSURE_DAX,
  KPI_DAX,
  RISK_BANDS_DAX,
  SEGMENT_RISK_DAX,
  mapCampaigns,
  mapKpis,
  mapRiskBands,
  mapSegments,
} from '@/services/queries';

export interface PanelProps {
  /** Sends a question to the supervisor. Wired by AgentPage; disabled while one is in flight. */
  onAsk: (question: string) => void;
  busy: boolean;
}

/** Colours are per risk band and stay identical to the Power BI report, so the two read alike. */
const BAND_STYLE: Record<string, { bar: string; label: string }> = {
  Critical: { bar: '#dc2626', label: 'Critique' },
  High: { bar: '#ea580c', label: 'Élevé' },
  Medium: { bar: '#d97706', label: 'Moyen' },
  Low: { bar: '#059669', label: 'Faible' },
  Prospect: { bar: '#64748b', label: 'Prospect' },
};

function PanelTitle({ children, note }: { children: string; note?: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
        {children}
      </h2>
      {note && (
        <p className="mt-0.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
          {note}
        </p>
      )}
    </div>
  );
}

/** The affordance that tells the audience a chart is an entry point and not a picture. */
function AskHint() {
  return (
    <p className="mt-3 text-[0.6875rem]" style={{ color: 'var(--text-muted)' }}>
      ↑ Cliquez une ligne : le chiffre part au superviseur, qui va chercher le pourquoi dans les
      verbatims.
    </p>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════════════
   Retention — the at-risk cohort and its signals
   ══════════════════════════════════════════════════════════════════════════════════════ */

function RetentionPanels({ onAsk, busy }: PanelProps) {
  const kpis = useDax(KPI_DAX, mapKpis);
  const bands = useDax(RISK_BANDS_DAX, mapRiskBands);
  const max = Math.max(1, ...(bands.data ?? []).map((b) => b.customers));

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle note="Mesures évaluées par SM_Marketing_Analytics, en Direct Lake.">
          La cohorte à risque
        </PanelTitle>
        <QueryState loading={kpis.loading} error={kpis.error} onRetry={kpis.reload}>
          {kpis.data && (
            <div className="grid gap-3 sm:grid-cols-2">
              <KpiCard
                label="Clients à risque"
                value={fmtInt(kpis.data.atRisk)}
                measure="[Customers at Risk]"
                tone="alert"
              />
              <KpiCard
                label="Part des acheteurs"
                value={fmtPct(kpis.data.atRiskPct)}
                measure="[At Risk %]"
                hint="Dénominateur : les acheteurs, pas la base totale"
                tone="alert"
              />
              <KpiCard
                label="CLV à risque"
                value={fmtEur(kpis.data.clvAtRisk)}
                measure="[CLV at Risk]"
                tone="alert"
              />
              <KpiCard
                label="Désabonnés"
                value={fmtInt(kpis.data.unsubscribed)}
                measure="[Unsubscribed Customers]"
              />
            </div>
          )}
        </QueryState>
      </section>

      <section>
        <PanelTitle note="crm_customer_profile — colonne risk_band.">
          Répartition par bande de risque
        </PanelTitle>
        <QueryState loading={bands.loading} error={bands.error} onRetry={bands.reload}>
          <div className="glass space-y-2 rounded-xl p-4">
            {(bands.data ?? []).map((b) => {
              const style = BAND_STYLE[b.band] ?? { bar: 'var(--accent)', label: b.band };
              const prospect = b.band === 'Prospect';
              return (
                <button
                  key={b.band}
                  disabled={busy || prospect}
                  onClick={() =>
                    onAsk(
                      `Dans crm_customer_profile, les clients dont la colonne risk_band vaut "${b.band}" sont ${fmtInt(b.customers)}. Que reprochent-ils dans leurs verbatims, et quelle action de rétention recommandes-tu ?`
                    )
                  }
                  className="block w-full rounded-lg px-2 py-1.5 text-left transition enabled:hover:bg-[var(--accent-soft)] disabled:cursor-default disabled:opacity-70"
                >
                  <div className="flex items-baseline justify-between text-xs">
                    <span className="font-medium" style={{ color: 'var(--text-primary)' }}>
                      {style.label}
                    </span>
                    <span className="tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                      {fmtInt(b.customers)} clients · {fmtEur(b.clv)}
                    </span>
                  </div>
                  <div
                    className="mt-1 h-2 w-full overflow-hidden rounded-full"
                    style={{ background: 'var(--wf-fill, rgba(148,163,184,0.12))' }}
                  >
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${(b.customers / max) * 100}%`,
                        background: style.bar,
                      }}
                    />
                  </div>
                </button>
              );
            })}
            <p className="pt-1 text-[0.6875rem]" style={{ color: 'var(--text-muted)' }}>
              Prospect = 0 par construction : sans commande, il n’y a pas d’attrition à mesurer,
              seulement une conversion.
            </p>
          </div>
          <AskHint />
        </QueryState>
      </section>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════════════
   Marketing — email pressure per campaign
   ══════════════════════════════════════════════════════════════════════════════════════ */

const PRESSURE_FACTOR = 1.5;
const UNSUB_FACTOR = 2;

function median(xs: number[]): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function MarketingPanels({ onAsk, busy }: PanelProps) {
  const campaigns = useDax(CAMPAIGN_PRESSURE_DAX, mapCampaigns);
  const rows = campaigns.data ?? [];

  const medSends = median(rows.map((c) => c.sendsPerCustomer));
  const medUnsub = median(rows.map((c) => c.unsubscribeRate));
  // Derived, never named: the outlier has to emerge from the arithmetic during the demo.
  const isOutlier = (c: (typeof rows)[number]) =>
    c.sendsPerCustomer > medSends * PRESSURE_FACTOR && c.unsubscribeRate > medUnsub * UNSUB_FACTOR;
  const outliers = rows.filter(isOutlier);

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle note="marketing_campaigns × marketing_events — envois par client et taux de désabonnement.">
          Pression e-mail par campagne
        </PanelTitle>

        <QueryState loading={campaigns.loading} error={campaigns.error} onRetry={campaigns.reload}>
          {outliers.length > 0 && (
            <div
              className="mb-3 rounded-xl border p-3 text-xs"
              style={{
                background: 'var(--warn-bg)',
                borderColor: 'var(--warn-border)',
                color: 'var(--warn-text)',
              }}
            >
              <span className="font-semibold">
                {outliers.length === 1 ? 'Une campagne sort' : `${outliers.length} campagnes sortent`}{' '}
                de la distribution
              </span>{' '}
              — plus de {PRESSURE_FACTOR}× la médiane d’envois par client ({fmtDecShort(medSends)})
              et plus de {UNSUB_FACTOR}× la médiane de désabonnement ({fmtPct(medUnsub)}). Le seuil
              est calculé, pas écrit en dur.
            </div>
          )}

          <div className="glass overflow-hidden rounded-xl">
            <table className="w-full text-left text-xs">
              <thead>
                <tr style={{ color: 'var(--text-muted)' }}>
                  <th className="px-3 py-2 font-semibold">Campagne</th>
                  <th className="px-3 py-2 text-right font-semibold">Envois/client</th>
                  <th className="px-3 py-2 text-right font-semibold">Désab.</th>
                  <th className="px-3 py-2 text-right font-semibold">Ouverture</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const flagged = isOutlier(c);
                  return (
                    <tr
                      key={c.id}
                      onClick={() => {
                        if (busy) return;
                        onAsk(
                          `La campagne « ${c.name} » envoie ${fmtDecShort(c.sendsPerCustomer)} messages par client contacté pour un taux de désabonnement de ${fmtPct(c.unsubscribeRate)} (marketing_events). Explique cet écart et cite ce que disent les clients qui l’ont reçue.`
                        );
                      }}
                      className={`cursor-pointer border-t transition hover:bg-[var(--accent-soft)] ${busy ? 'pointer-events-none opacity-60' : ''}`}
                      style={{
                        borderColor: 'var(--border)',
                        background: flagged ? 'var(--warn-bg)' : undefined,
                      }}
                    >
                      <td
                        className="px-3 py-2 font-medium"
                        style={{ color: 'var(--text-primary)' }}
                      >
                        {flagged && <span className="mr-1">⚠</span>}
                        {c.name}
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{
                          color: flagged ? '#dc2626' : 'var(--text-secondary)',
                          fontWeight: flagged ? 600 : 400,
                        }}
                      >
                        {fmtDecShort(c.sendsPerCustomer)}
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{
                          color: flagged ? '#dc2626' : 'var(--text-secondary)',
                          fontWeight: flagged ? 600 : 400,
                        }}
                      >
                        {fmtPct(c.unsubscribeRate)}
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        {fmtPct(c.openRate)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <AskHint />
        </QueryState>
      </section>
    </div>
  );
}

/** One decimal, no currency — used for "sends per customer". */
function fmtDecShort(v: number): string {
  return v.toFixed(1).replace('.', ',');
}

/* ══════════════════════════════════════════════════════════════════════════════════════
   Commerce — where the money at risk sits
   ══════════════════════════════════════════════════════════════════════════════════════ */

function CommercePanels({ onAsk, busy }: PanelProps) {
  const kpis = useDax(KPI_DAX, mapKpis);
  const segments = useDax(SEGMENT_RISK_DAX, mapSegments);
  const total = kpis.data?.clvAtRisk ?? 0;

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle note="Valeur exposée, telle que la calcule le modèle sémantique.">
          Impact business
        </PanelTitle>
        <QueryState loading={kpis.loading} error={kpis.error} onRetry={kpis.reload}>
          {kpis.data && (
            <div className="grid gap-3 sm:grid-cols-2">
              <KpiCard
                label="CLV à risque"
                value={fmtEur(kpis.data.clvAtRisk)}
                measure="[CLV at Risk]"
                tone="alert"
              />
              <KpiCard
                label="CA à risque"
                value={fmtEur(kpis.data.revenueAtRisk)}
                measure="[Revenue at Risk]"
                tone="alert"
              />
            </div>
          )}
        </QueryState>
      </section>

      <section>
        <PanelTitle note="crm_segments — part de la CLV à risque portée par chaque segment.">
          Exposition par segment
        </PanelTitle>
        <QueryState loading={segments.loading} error={segments.error} onRetry={segments.reload}>
          <div className="glass overflow-hidden rounded-xl">
            <table className="w-full text-left text-xs">
              <thead>
                <tr style={{ color: 'var(--text-muted)' }}>
                  <th className="px-3 py-2 font-semibold">Segment</th>
                  <th className="px-3 py-2 text-right font-semibold">À risque</th>
                  <th className="px-3 py-2 text-right font-semibold">CLV exposée</th>
                  <th className="w-24 px-3 py-2 font-semibold">Part</th>
                </tr>
              </thead>
              <tbody>
                {(segments.data ?? []).map((s) => {
                  const share = total > 0 ? s.clvAtRisk / total : 0;
                  const dominant = share > 0.25;
                  return (
                    <tr
                      key={s.segment}
                      onClick={() => {
                        if (busy) return;
                        onAsk(
                          `Le segment « ${s.segment} » (crm_segments) porte ${fmtEur(s.clvAtRisk)} de CLV à risque sur ${fmtInt(s.atRisk)} clients. Qu’est-ce qui explique cette concentration, et que disent ces clients dans leurs verbatims ?`
                        );
                      }}
                      className={`cursor-pointer border-t transition hover:bg-[var(--accent-soft)] ${busy ? 'pointer-events-none opacity-60' : ''}`}
                      style={{ borderColor: 'var(--border)' }}
                    >
                      <td
                        className="px-3 py-2 font-medium"
                        style={{ color: 'var(--text-primary)' }}
                      >
                        {s.segment}
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        {fmtInt(s.atRisk)}
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        style={{
                          color: dominant ? '#dc2626' : 'var(--text-secondary)',
                          fontWeight: dominant ? 600 : 400,
                        }}
                      >
                        {fmtEur(s.clvAtRisk)}
                      </td>
                      <td className="px-3 py-2">
                        <div
                          className="h-2 w-full overflow-hidden rounded-full"
                          style={{ background: 'rgba(148,163,184,0.18)' }}
                        >
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.min(100, share * 100)}%`,
                              background: dominant ? '#dc2626' : 'var(--accent)',
                            }}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[0.6875rem]" style={{ color: 'var(--text-muted)' }}>
            Les segments se recoupent : un même client peut appartenir à plusieurs d’entre eux. La
            somme des lignes dépasse donc volontairement la CLV à risque totale, et la part est un
            rapport à cette mesure non filtrée.
          </p>
          <AskHint />
        </QueryState>
      </section>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════════════
   Direction — the portfolio, top down
   ══════════════════════════════════════════════════════════════════════════════════════ */

function DirectionPanels({ onAsk, busy }: PanelProps) {
  const kpis = useDax(KPI_DAX, mapKpis);
  const bands = useDax(RISK_BANDS_DAX, mapRiskBands);
  const max = Math.max(1, ...(bands.data ?? []).map((b) => b.customers));

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle note="Huit mesures du modèle sémantique, lues et non recalculées.">
          Le portefeuille en un écran
        </PanelTitle>
        <QueryState loading={kpis.loading} error={kpis.error} onRetry={kpis.reload}>
          {kpis.data && (
            <div className="grid gap-3 sm:grid-cols-2">
              <KpiCard
                label="Clients"
                value={fmtInt(kpis.data.totalCustomers)}
                measure="[Total Customers]"
              />
              <KpiCard label="Acheteurs" value={fmtInt(kpis.data.buyers)} measure="[Buyers]" />
              <KpiCard
                label="Clients à risque"
                value={fmtInt(kpis.data.atRisk)}
                measure="[Customers at Risk]"
                tone="alert"
              />
              <KpiCard
                label="Part des acheteurs"
                value={fmtPct(kpis.data.atRiskPct)}
                measure="[At Risk %]"
                hint="Rapportée aux acheteurs"
                tone="alert"
              />
              <KpiCard
                label="CLV à risque"
                value={fmtEur(kpis.data.clvAtRisk)}
                measure="[CLV at Risk]"
                tone="alert"
              />
              <KpiCard
                label="CA à risque"
                value={fmtEur(kpis.data.revenueAtRisk)}
                measure="[Revenue at Risk]"
                tone="alert"
              />
              <KpiCard
                label="Désabonnés"
                value={fmtInt(kpis.data.unsubscribed)}
                measure="[Unsubscribed Customers]"
              />
              <KpiCard
                label="Score de churn moyen"
                value={kpis.data.avgChurnScore.toFixed(1).replace('.', ',')}
                measure="[Avg Churn Score]"
              />
            </div>
          )}
        </QueryState>
      </section>

      <section>
        <PanelTitle>Où se concentre le risque</PanelTitle>
        <QueryState loading={bands.loading} error={bands.error} onRetry={bands.reload}>
          <div className="glass space-y-2 rounded-xl p-4">
            {(bands.data ?? []).map((b) => {
              const style = BAND_STYLE[b.band] ?? { bar: 'var(--accent)', label: b.band };
              const prospect = b.band === 'Prospect';
              return (
                <button
                  key={b.band}
                  disabled={busy || prospect}
                  onClick={() =>
                    onAsk(
                      `Le portefeuille compte ${fmtInt(b.customers)} clients en risk_band "${b.band}" (crm_customer_profile), soit ${fmtEur(b.clv)} de CLV. Quel est l’enjeu pour la direction et sur quoi agir en priorité ?`
                    )
                  }
                  className="block w-full rounded-lg px-2 py-1.5 text-left transition enabled:hover:bg-[var(--accent-soft)] disabled:cursor-default disabled:opacity-70"
                >
                  <div className="flex items-baseline justify-between text-xs">
                    <span className="font-medium" style={{ color: 'var(--text-primary)' }}>
                      {style.label}
                    </span>
                    <span className="tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                      {fmtInt(b.customers)} · {fmtEur(b.clv)}
                    </span>
                  </div>
                  <div
                    className="mt-1 h-2 w-full overflow-hidden rounded-full"
                    style={{ background: 'rgba(148,163,184,0.18)' }}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${(b.customers / max) * 100}%`, background: style.bar }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
          <AskHint />
        </QueryState>
      </section>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════════════ */

const BY_PERSONA: Record<string, (p: PanelProps) => ReactElement> = {
  retention: RetentionPanels,
  marketing: MarketingPanels,
  commerce: CommercePanels,
  direction: DirectionPanels,
};

/**
 * Renders the visuals owned by one persona. A persona with no panel registered falls back to
 * the portfolio view rather than to an empty column — an empty half-screen reads as a bug.
 */
export function PersonaPanels({ personaKey, onAsk, busy }: PanelProps & { personaKey: string }) {
  const Panels = BY_PERSONA[personaKey] ?? DirectionPanels;
  return <Panels onAsk={onAsk} busy={busy} />;
}
