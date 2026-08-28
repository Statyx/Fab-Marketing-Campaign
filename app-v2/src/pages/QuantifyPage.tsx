/**
 * Step 3 — Quantify: where the exposed value sits.
 *
 * One trap this screen has to avoid: segments overlap. A customer belongs to several of them,
 * so the per-segment figures deliberately do **not** add up to the total — summing the column
 * would double-count and produce a number larger than the whole population. The total shown is
 * therefore the model's own unfiltered `[CLV at Risk]`, evaluated separately, and the share
 * column is a ratio to that total rather than to the column sum. The caption says so, because a
 * set of percentages that don't reach 100 % looks like a bug until you know why.
 */
import { AppShell } from '@/components/AppShell';
import { KpiCard } from '@/components/KpiCard';
import { QueryState } from '@/components/QueryState';
import { useDax } from '@/hooks/useDax';
import { fmtEur, fmtInt, fmtPct } from '@/lib/format';
import { KPI_DAX, SEGMENT_RISK_DAX, mapKpis, mapSegments } from '@/services/queries';

export function QuantifyPage() {
  const kpis = useDax(KPI_DAX, mapKpis);
  const segments = useDax(SEGMENT_RISK_DAX, mapSegments);

  const totalClvAtRisk = kpis.data?.clvAtRisk ?? 0;
  const totalAtRisk = kpis.data?.atRisk ?? 0;
  const maxClv = Math.max(1, ...(segments.data?.map((s) => s.clvAtRisk) ?? [1]));

  return (
    <AppShell
      title="3. Quantifier"
      intro="La valeur exposée, ventilée par segment. Un client appartient à plusieurs segments : les lignes se recoupent et ne s'additionnent donc pas au total."
    >
      <QueryState loading={kpis.loading} error={kpis.error} onRetry={kpis.reload}>
        {kpis.data && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <KpiCard
              label="CLV à risque"
              value={fmtEur(kpis.data.clvAtRisk)}
              measure="[CLV at Risk]"
              hint="total, sans filtre de segment"
              tone="alert"
            />
            <KpiCard
              label="CA à risque"
              value={fmtEur(kpis.data.revenueAtRisk)}
              measure="[Revenue at Risk]"
            />
            <KpiCard
              label="Clients concernés"
              value={fmtInt(kpis.data.atRisk)}
              measure="[Customers at Risk]"
            />
          </div>
        )}
      </QueryState>

      <section className="mt-8 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-6 py-4">
          <h2 className="text-sm font-semibold text-slate-900">Exposition par segment</h2>
          <p className="mt-1 text-xs text-slate-500">
            Part calculée sur le total non filtré ({fmtEur(totalClvAtRisk)}) — les segments se
            chevauchant, la colonne ne totalise pas 100 %.
          </p>
        </div>

        <QueryState
          loading={segments.loading}
          error={segments.error}
          empty={segments.data?.length === 0}
          onRetry={segments.reload}
        >
          {segments.data && (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                  <th className="px-6 py-3 text-left font-medium">Segment</th>
                  <th className="px-4 py-3 text-right font-medium">Membres</th>
                  <th className="px-4 py-3 text-right font-medium">À risque</th>
                  <th className="px-4 py-3 text-right font-medium">CLV à risque</th>
                  <th className="px-6 py-3 text-left font-medium">Part du total</th>
                </tr>
              </thead>
              <tbody>
                {segments.data.map((s) => {
                  const share = totalClvAtRisk > 0 ? s.clvAtRisk / totalClvAtRisk : 0;
                  const dominant = share > 0.25;
                  return (
                    <tr
                      key={s.segment}
                      className={`border-b border-slate-100 last:border-0 ${
                        dominant ? 'bg-orange-50' : ''
                      }`}
                    >
                      <td className="px-6 py-2.5">
                        <span
                          className={dominant ? 'font-semibold text-orange-900' : 'text-slate-800'}
                        >
                          {s.segment}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                        {fmtInt(s.members)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
                        {fmtInt(s.atRisk)}
                        {totalAtRisk > 0 && (
                          <span className="ml-1 text-xs text-slate-400">
                            ({fmtPct(s.atRisk / totalAtRisk, 0)})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums font-medium text-slate-900">
                        {fmtEur(s.clvAtRisk)}
                      </td>
                      <td className="px-6 py-2.5">
                        <div className="flex items-center gap-3">
                          <div className="h-2 w-32 overflow-hidden rounded bg-slate-100">
                            <div
                              className={`h-full ${dominant ? 'bg-orange-400' : 'bg-slate-400'}`}
                              style={{ width: `${(s.clvAtRisk / maxClv) * 100}%` }}
                            />
                          </div>
                          <span className="w-12 text-right text-xs tabular-nums text-slate-500">
                            {fmtPct(share, 0)}
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </QueryState>
      </section>

      <p className="mt-3 text-xs text-slate-500">
        Mesures utilisées : <span className="font-mono">[Segment Memberships]</span>,{' '}
        <span className="font-mono">[Customers at Risk]</span>,{' '}
        <span className="font-mono">[CLV at Risk]</span>,{' '}
        <span className="font-mono">[Revenue at Risk]</span>, traversées via la table de liaison{' '}
        <span className="font-mono">crm_customer_segments</span>.
      </p>
    </AppShell>
  );
}
