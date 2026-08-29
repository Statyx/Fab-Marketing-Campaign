/**
 * A single figure, with the measure that produced it named underneath.
 *
 * Showing the measure name is not decoration: every number on these screens is evaluated by
 * SM_Marketing_Analytics, and printing which measure it came from is what lets someone
 * reproduce it in Power BI instead of taking the app's word for it.
 *
 * Two changes when the arc screens were folded into the personas:
 *
 *  - **Themed.** It used to hardcode `bg-white` / `text-slate-900`, which is why the guided
 *    screens stayed a light rectangle in dark mode while everything around them repainted.
 *    A card that ignores the theme is not a styling detail — it was half the app unreadable
 *    at night.
 *  - **Compact.** It now sits in a column beside a conversation rather than across a full
 *    page, so the callout drops from `text-3xl` to `text-2xl`.
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
    <div className="glass rounded-xl p-4">
      <p
        className="text-[0.625rem] font-semibold uppercase tracking-wide"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-bold tabular-nums" style={{ color: TONES[tone] }}>
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-[0.6875rem]" style={{ color: 'var(--text-secondary)' }}>
          {hint}
        </p>
      )}
      <p
        className="mt-2.5 border-t pt-1.5 font-mono text-[0.625rem]"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        {measure}
      </p>
    </div>
  );
}
