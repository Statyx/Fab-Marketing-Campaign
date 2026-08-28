/**
 * Step 2 — Diagnose: which campaign is generating the pressure.
 *
 * The culprit is *not* named anywhere in this file. Every campaign is ranked on the same
 * metrics and the outlier rule is arithmetic — a campaign is flagged when it sits far above the
 * median of its peers. A screen that hardcoded "CAMP_007" would demo identically and prove
 * nothing: the point of a root-cause screen is that the cause emerges from the data, so that
 * the same screen still works when the data changes.
 *
 * The rule and its thresholds are printed on screen for the same reason.
 */
import { AppShell } from '@/components/AppShell';
import { QueryState } from '@/components/QueryState';
import { useDax } from '@/hooks/useDax';
import { fmtDec, fmtInt, fmtPct } from '@/lib/format';
import { CAMPAIGN_PRESSURE_DAX, mapCampaigns, type CampaignPressure } from '@/services/queries';

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

const PRESSURE_FACTOR = 1.5;
const UNSUB_FACTOR = 2;

interface Thresholds {
  pressure: number;
  unsub: number;
}

function thresholds(rows: CampaignPressure[]): Thresholds {
  return {
    pressure: median(rows.map((r) => r.sendsPerCustomer)) * PRESSURE_FACTOR,
    unsub: median(rows.map((r) => r.unsubscribeRate)) * UNSUB_FACTOR,
  };
}

const isOutlier = (c: CampaignPressure, t: Thresholds): boolean =>
  c.sendsPerCustomer > t.pressure || c.unsubscribeRate > t.unsub;

export function DiagnosePage() {
  const campaigns = useDax(CAMPAIGN_PRESSURE_DAX, mapCampaigns);
  const t = campaigns.data ? thresholds(campaigns.data) : null;
  const flagged = campaigns.data?.filter((c) => t && isOutlier(c, t)) ?? [];

  return (
    <AppShell
      title="2. Diagnostiquer"
      intro="Toutes les campagnes sont classées sur les mêmes indicateurs de pression. Aucune n'est désignée à l'avance : celles qui ressortent sont celles qui dépassent la médiane de leurs pairs."
    >
      <QueryState
        loading={campaigns.loading}
        error={campaigns.error}
        empty={campaigns.data?.length === 0}
        onRetry={campaigns.reload}
      >
        {campaigns.data && t && (
          <>
            {flagged.length > 0 && (
              <div className="rounded-xl border border-orange-200 bg-orange-50 p-5">
                <p className="text-sm font-semibold text-orange-900">
                  {flagged.length === 1
                    ? '1 campagne se détache'
                    : `${flagged.length} campagnes se détachent`}
                </p>
                <ul className="mt-2 space-y-1 text-sm text-orange-800">
                  {flagged.map((c) => (
                    <li key={c.id}>
                      <span className="font-medium">{c.name}</span>{' '}
                      <span className="font-mono text-xs">({c.id})</span> —{' '}
                      {fmtDec(c.sendsPerCustomer)} envois par client,{' '}
                      {fmtPct(c.unsubscribeRate, 2)} de désabonnement,{' '}
                      {fmtPct(c.openRate)} d'ouverture
                    </li>
                  ))}
                </ul>
                <p className="mt-3 border-t border-orange-200 pt-2 text-xs text-orange-700">
                  Règle appliquée : plus de {fmtDec(t.pressure)} envois par client (
                  {PRESSURE_FACTOR}× la médiane) ou plus de {fmtPct(t.unsub, 2)} de désabonnement (
                  {UNSUB_FACTOR}× la médiane).
                </p>
              </div>
            )}

            <div className="mt-6 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-3 text-left font-medium">Campagne</th>
                    <th className="px-4 py-3 text-right font-medium">Envois</th>
                    <th className="px-4 py-3 text-right font-medium">Clients touchés</th>
                    <th className="px-4 py-3 text-right font-medium">Envois / client</th>
                    <th className="px-4 py-3 text-right font-medium">Désabonnement</th>
                    <th className="px-4 py-3 text-right font-medium">Ouverture</th>
                    <th className="px-4 py-3 text-right font-medium">Clic</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.data.map((c) => {
                    const flag = isOutlier(c, t);
                    return (
                      <tr
                        key={c.id}
                        className={`border-b border-slate-100 last:border-0 ${
                          flag ? 'bg-orange-50' : ''
                        }`}
                      >
                        <td className="px-4 py-2.5">
                          <span className={flag ? 'font-semibold text-orange-900' : 'text-slate-800'}>
                            {c.name}
                          </span>
                          <span className="ml-2 font-mono text-[11px] text-slate-400">{c.id}</span>
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                          {fmtInt(c.sends)}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                          {fmtInt(c.contacted)}
                        </td>
                        <td
                          className={`px-4 py-2.5 text-right tabular-nums ${
                            c.sendsPerCustomer > t.pressure
                              ? 'font-semibold text-orange-700'
                              : 'text-slate-600'
                          }`}
                        >
                          {fmtDec(c.sendsPerCustomer)}
                        </td>
                        <td
                          className={`px-4 py-2.5 text-right tabular-nums ${
                            c.unsubscribeRate > t.unsub
                              ? 'font-semibold text-red-700'
                              : 'text-slate-600'
                          }`}
                        >
                          {fmtPct(c.unsubscribeRate, 2)}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                          {fmtPct(c.openRate)}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                          {fmtPct(c.clickRate)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <p className="mt-3 text-xs text-slate-500">
              Mesures utilisées : <span className="font-mono">[Total Sends]</span>,{' '}
              <span className="font-mono">[Customers Contacted]</span>,{' '}
              <span className="font-mono">[Sends per Customer]</span>,{' '}
              <span className="font-mono">[Unsubscribe Rate]</span>,{' '}
              <span className="font-mono">[Open Rate]</span>,{' '}
              <span className="font-mono">[Click Through Rate]</span>.
            </p>
          </>
        )}
      </QueryState>
    </AppShell>
  );
}
