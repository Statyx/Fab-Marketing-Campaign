/**
 * A single figure. The measure that produced it is available on hover, not printed on its face.
 *
 * It used to be printed — `[Customers at Risk]` in mono under every card — on the argument that
 * naming the measure is what lets someone reproduce the figure in Power BI. That argument holds;
 * the placement did not. Twenty cards each carrying an English identifier read as instrumentation
 * on a screen shown to a marketing audience, and the app already has the right answer to this
 * elsewhere: provenance is *available*, never *on stage* — the `SOURCE` fold under an answer, the
 * technical detail behind a button on a failure. A `title` is that same fold at card size.
 *
 * Two changes when the arc screens were folded into the personas:
 *
 *  - **Themed.** It used to hardcode `bg-white` / `text-slate-900`, which is why the guided
 *    screens stayed a light rectangle in dark mode while everything around them repainted.
 *    A card that ignores the theme is not a styling detail — it was half the app unreadable
 *    at night.
 *  - **Compact.** It now sits in a column beside a conversation rather than across a full
 *    page, so the callout drops from `text-3xl` to `text-xl`. The first cut kept `text-2xl`
 *    and read as oversized once four cards sat on one row — a KPI grid is scanned, and a
 *    figure only has to be the largest thing in its own card, not on the screen.
 */
interface Props {
  label: string;
  value: string;
  measure: string;
  hint?: string;
  tone?: 'default' | 'alert' | 'good';
}

const TONES: Record<NonNullable<Props['tone']>, string> = {
  default: 'var(--text-primary)',
  alert: '#dc2626',
  good: '#059669',
};

export function KpiCard({ label, value, measure, hint, tone = 'default' }: Props) {
  return (
    <div className="glass rounded-xl p-4" title={`Mesure ${measure} — modèle sémantique`}>
      <p
        className="text-[0.625rem] font-semibold uppercase tracking-wide"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </p>
      <p className="mt-1.5 text-xl font-bold tabular-nums" style={{ color: TONES[tone] }}>
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-[0.6875rem]" style={{ color: 'var(--text-secondary)' }}>
          {hint}
        </p>
      )}
    </div>
  );
}
