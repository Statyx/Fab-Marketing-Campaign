/**
 * Step 1 — Detect: how much value is exposed, and how it is distributed.
 *
 * Nothing here is computed in the browser. Even `At Risk %` is a model measure, which matters
 * because its denominator is *buyers*, not the whole customer base: a contact who never ordered
 * has a conversion problem, not a churn problem. Recomputing it here against `Total Customers`
 * would silently produce 6.9 % instead of 7.8 % and quietly contradict the report.
 */
import { KpiCard } from '@/components/KpiCard';
import { AppShell } from '@/components/AppShell';
import { QueryState } from '@/components/QueryState';
import { useDax } from '@/hooks/useDax';
import { fmtEur, fmtInt, fmtPct } from '@/lib/format';
import {
  KPI_DAX,
  RISK_BANDS_DAX,
  mapKpis,
  mapRiskBands,
  type RiskBand,
} from '@/services/queries';

const BAND_STYLE: Record<string, { bar: string; dot: string }> = {
  Critical: { bar: 'bg-red-500', dot: 'bg-red-500' },
  High: { bar: 'bg-orange-400', dot: 'bg-orange-400' },
  Medium: { bar: 'bg-amber-300', dot: 'bg-amber-300' },
  Low: { bar: 'bg-emerald-400', dot: 'bg-emerald-400' },
  Prospect: { bar: 'bg-slate-300', dot: 'bg-slate-300' },
};

function BandRow({ band, max }: { band: RiskBand; max: number }) {
  const style = BAND_STYLE[band.band] ?? BAND_STYLE.Prospect;
  return (
    <div className="flex items-center gap-4 py-2">
      <div className="flex w-28 shrink-0 items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} />
        <span className="text-sm text-slate-700">{band.band}</span>
      </div>
      <div className="h-6 flex-1 overflow-hidden rounded bg-slate-100">
        <div
          className={`h-full ${style.bar}`}
          style={{ width: `${max > 0 ? (band.customers / max) * 100 : 0}%` }}
        />
      </div>
      <span className="w-20 shrink-0 text-right text-sm tabular-nums text-slate-900">
        {fmtInt(band.customers)}
      </span>
      <span className="w-28 shrink-0 text-right text-sm tabular-nums text-slate-500">
        {fmtEur(band.clv)}
      </span>
    </div>
  );
}

export function DetectPage() {
  const kpis = useDax(KPI_DAX, mapKpis);
  const bands = useDax(RISK_BANDS_DAX, mapRiskBands);

  return (
    <AppShell
      title="1. Détecter"
      intro="Combien de clients sont exposés au churn, et quelle valeur cela représente. « À risque » veut dire ici risk_band ∈ {High, Critical} — la définition est nommée parce qu'il en existe trois défendables dans ce modèle."
    >
      <QueryState loading={kpis.loading} error={kpis.error} onRetry={kpis.reload}>
        {kpis.data && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <KpiCard
              label="Clients à risque"
              value={fmtInt(kpis.data.atRisk)}
              measure="[Customers at Risk]"
              hint="risk_band ∈ {High, Critical}"
              tone="alert"
            />
            <KpiCard
              label="Part des acheteurs"
              value={fmtPct(kpis.data.atRiskPct)}
              measure="[At Risk %]"
              hint={`sur ${fmtInt(kpis.data.buyers)} acheteurs`}
              tone="alert"
            />
            <KpiCard
              label="CLV à risque"
              value={fmtEur(kpis.data.clvAtRisk)}
              measure="[CLV at Risk]"
              hint="valeur vie client exposée"
            />
            <KpiCard
              label="Chiffre d'affaires à risque"
              value={fmtEur(kpis.data.revenueAtRisk)}
              measure="[Revenue at Risk]"
              hint="CA déjà réalisé par ces clients"
            />
            <KpiCard
              label="Clients profilés"
              value={fmtInt(kpis.data.totalCustomers)}
              measure="[Total Customers]"
            />
            <KpiCard
              label="Acheteurs"
              value={fmtInt(kpis.data.buyers)}
              measure="[Buyers]"
              hint="le churn ne concerne qu'eux"
            />
            <KpiCard
              label="Désabonnés"
              value={fmtInt(kpis.data.unsubscribed)}
              measure="[Unsubscribed Customers]"
              tone="alert"
            />
            <KpiCard
              label="Score de churn moyen"
              value={fmtInt(kpis.data.avgChurnScore)}
              measure="[Avg Churn Score]"
              hint="sur 100"
            />
          </div>
        )}
      </QueryState>

      <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Répartition par bande de risque</h2>
        <p className="mt-1 text-xs text-slate-500">
          Les prospects sont à 0 par construction : sans commande, il n'y a pas de churn à
          mesurer.
        </p>
        <div className="mt-4">
          <QueryState
            loading={bands.loading}
            error={bands.error}
            empty={bands.data?.length === 0}
            onRetry={bands.reload}
          >
            {bands.data && (
              <>
                <div className="flex items-center gap-4 border-b border-slate-100 pb-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">
                  <span className="w-28 shrink-0">Bande</span>
                  <span className="flex-1" />
                  <span className="w-20 shrink-0 text-right">Clients</span>
                  <span className="w-28 shrink-0 text-right">CLV totale</span>
                </div>
                {bands.data.map((b) => (
                  <BandRow
                    key={b.band}
                    band={b}
                    max={Math.max(...bands.data!.map((x) => x.customers))}
                  />
                ))}
              </>
            )}
          </QueryState>
        </div>
      </section>
    </AppShell>
  );
}
