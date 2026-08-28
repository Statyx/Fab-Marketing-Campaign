/**
 * Every DAX query the app runs, in one file.
 *
 * Two rules hold this together:
 *
 * 1. **Bind to the model's measures, never re-derive them.** `[Customers at Risk]`,
 *    `[CLV at Risk]`, `[Unsubscribe Rate]` and friends already exist in SM_Marketing_Analytics
 *    and are what the Power BI report and the data agent read. Writing an equivalent CALCULATE
 *    here would create a second definition of the same business rule, free to drift from the
 *    first — and drift that only shows up as two screens disagreeing in front of a customer.
 *    Checked: `[Customers at Risk]` returns 825, the same figure the Foundry supervisor
 *    produces through an entirely separate route.
 *
 * 2. **Keep the raw column keys here.** SUMMARIZECOLUMNS answers with keys like
 *    `crm_customer_profile[risk_band]` and `[CLV at Risk]`. Letting that spelling leak into
 *    components would scatter the coupling to the model across the UI; each query therefore
 *    ships with its own mapper and hands back a plain typed object.
 */
import type { DaxRow, DaxValue } from './powerbi';

const num = (v: DaxValue): number => (typeof v === 'number' ? v : Number(v ?? 0) || 0);
const str = (v: DaxValue): string => (v === null || v === undefined ? '' : String(v));

/* ----------------------------------------------------------------- Landing */

export interface LandingStats {
  customers: number;
  campaigns: number;
  segments: number;
  atRisk: number;
}

/**
 * The landing tiles. Counts come from the model rather than being typed into the page: a
 * hardcoded "12 000 clients" is a caption that keeps its value after the data changes, which is
 * exactly the kind of quiet lie this app exists not to tell.
 *
 * COUNTROWS is wrapped in COALESCE — it answers BLANK, not 0, on an empty table.
 */
export const LANDING_DAX = `
EVALUATE
ROW(
  "Customers", [Total Customers],
  "Campaigns", COALESCE(COUNTROWS('marketing_campaigns'), 0),
  "Segments", COALESCE(COUNTROWS('crm_segments'), 0),
  "AtRisk", [Customers at Risk]
)`;

export function mapLanding(rows: DaxRow[]): LandingStats {
  const r = rows[0] ?? {};
  return {
    customers: num(r['[Customers]']),
    campaigns: num(r['[Campaigns]']),
    segments: num(r['[Segments]']),
    atRisk: num(r['[AtRisk]']),
  };
}

/* ------------------------------------------------------------------ Detect */

export interface Kpis {
  atRisk: number;
  atRiskPct: number;
  clvAtRisk: number;
  revenueAtRisk: number;
  buyers: number;
  totalCustomers: number;
  unsubscribed: number;
  avgChurnScore: number;
}

export const KPI_DAX = `
EVALUATE
ROW(
  "AtRisk", [Customers at Risk],
  "AtRiskPct", [At Risk %],
  "CLVatRisk", [CLV at Risk],
  "RevenueAtRisk", [Revenue at Risk],
  "Buyers", [Buyers],
  "TotalCustomers", [Total Customers],
  "Unsubscribed", [Unsubscribed Customers],
  "AvgChurnScore", [Avg Churn Score]
)`;

export function mapKpis(rows: DaxRow[]): Kpis {
  const r = rows[0] ?? {};
  return {
    atRisk: num(r['[AtRisk]']),
    atRiskPct: num(r['[AtRiskPct]']),
    clvAtRisk: num(r['[CLVatRisk]']),
    revenueAtRisk: num(r['[RevenueAtRisk]']),
    buyers: num(r['[Buyers]']),
    totalCustomers: num(r['[TotalCustomers]']),
    unsubscribed: num(r['[Unsubscribed]']),
    avgChurnScore: num(r['[AvgChurnScore]']),
  };
}

export interface RiskBand {
  band: string;
  customers: number;
  clv: number;
  avgScore: number;
}

export const RISK_BANDS_DAX = `
EVALUATE
SUMMARIZECOLUMNS(
  'crm_customer_profile'[risk_band],
  "Customers", [Profiled Customers],
  "CLV", [Total CLV],
  "AvgScore", [Avg Churn Score]
)`;

/**
 * Band order is fixed here rather than sorted by value: these are ordinal, and letting the
 * data decide the order would reshuffle the chart every time the numbers move.
 */
const BAND_ORDER = ['Critical', 'High', 'Medium', 'Low', 'Prospect'];

export function mapRiskBands(rows: DaxRow[]): RiskBand[] {
  return rows
    .map((r) => ({
      band: str(r['crm_customer_profile[risk_band]']),
      customers: num(r['[Customers]']),
      clv: num(r['[CLV]']),
      avgScore: num(r['[AvgScore]']),
    }))
    .filter((b) => b.band !== '')
    .sort((a, b) => BAND_ORDER.indexOf(a.band) - BAND_ORDER.indexOf(b.band));
}

/* ---------------------------------------------------------------- Diagnose */

export interface CampaignPressure {
  id: string;
  name: string;
  sends: number;
  contacted: number;
  sendsPerCustomer: number;
  unsubscribeRate: number;
  openRate: number;
  clickRate: number;
}

/**
 * The diagnosis screen's whole point is that the culprit campaign has to *emerge* from the
 * pressure metrics, not be named in advance. So this query ranks every campaign on the same
 * footing and no campaign id appears anywhere in it.
 */
export const CAMPAIGN_PRESSURE_DAX = `
EVALUATE
SUMMARIZECOLUMNS(
  'marketing_campaigns'[campaign_id],
  'marketing_campaigns'[campaign_name],
  "Sends", [Total Sends],
  "Contacted", [Customers Contacted],
  "SendsPerCustomer", [Sends per Customer],
  "UnsubscribeRate", [Unsubscribe Rate],
  "OpenRate", [Open Rate],
  "ClickRate", [Click Through Rate]
)
ORDER BY [SendsPerCustomer] DESC, [UnsubscribeRate] DESC`;

export function mapCampaigns(rows: DaxRow[]): CampaignPressure[] {
  return rows
    .map((r) => ({
      id: str(r['marketing_campaigns[campaign_id]']),
      name: str(r['marketing_campaigns[campaign_name]']),
      sends: num(r['[Sends]']),
      contacted: num(r['[Contacted]']),
      sendsPerCustomer: num(r['[SendsPerCustomer]']),
      unsubscribeRate: num(r['[UnsubscribeRate]']),
      openRate: num(r['[OpenRate]']),
      clickRate: num(r['[ClickRate]']),
    }))
    .filter((c) => c.id !== '');
}

/* ---------------------------------------------------------------- Quantify */

export interface SegmentRisk {
  segment: string;
  members: number;
  atRisk: number;
  clvAtRisk: number;
  revenueAtRisk: number;
}

/**
 * Segments reach the profile through the crm_customer_segments bridge, so this is a
 * many-to-many traversal — verified against the live model rather than assumed.
 */
export const SEGMENT_RISK_DAX = `
EVALUATE
SUMMARIZECOLUMNS(
  'crm_segments'[segment_name],
  "Members", [Segment Memberships],
  "AtRisk", [Customers at Risk],
  "CLVatRisk", [CLV at Risk],
  "RevenueAtRisk", [Revenue at Risk]
)
ORDER BY [CLVatRisk] DESC`;

export function mapSegments(rows: DaxRow[]): SegmentRisk[] {
  return rows
    .map((r) => ({
      segment: str(r['crm_segments[segment_name]']),
      members: num(r['[Members]']),
      atRisk: num(r['[AtRisk]']),
      clvAtRisk: num(r['[CLVatRisk]']),
      revenueAtRisk: num(r['[RevenueAtRisk]']),
    }))
    // The relationship yields a blank member row; it is an artefact of the join, not a segment.
    .filter((s) => s.segment !== '');
}
