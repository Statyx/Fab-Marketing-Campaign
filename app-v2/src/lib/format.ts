/** fr-FR display formatting. Values arrive already aggregated by the semantic model. */

const int = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const eur = new Intl.NumberFormat('fr-FR', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 0,
});

export const fmtInt = (v: number): string => int.format(v);
export const fmtEur = (v: number): string => eur.format(v);

export const fmtPct = (v: number, digits = 1): string =>
  `${new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v * 100)} %`;

export const fmtDec = (v: number, digits = 2): string =>
  new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
