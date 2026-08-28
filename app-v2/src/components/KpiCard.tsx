/**
 * A single figure, with the measure that produced it named underneath.
 *
 * Showing the measure name is not decoration: every number on these screens is evaluated by
 * SM_Marketing_Analytics, and printing which measure it came from is what lets someone
 * reproduce it in Power BI instead of taking the app's word for it.
 */
interface Props {
  label: string;
  value: string;
  measure: string;
  hint?: string;
  tone?: 'default' | 'alert' | 'good';
}

const TONES: Record<NonNullable<Props['tone']>, string> = {
  default: 'text-slate-900',
  alert: 'text-red-600',
  good: 'text-emerald-600',
};

export function KpiCard({ label, value, measure, hint, tone = 'default' }: Props) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${TONES[tone]}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      <p className="mt-3 border-t border-slate-100 pt-2 font-mono text-[10px] text-slate-400">
        {measure}
      </p>
    </div>
  );
}
