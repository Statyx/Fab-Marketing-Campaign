# Fab-Marketing-Campaign

> **Customer 360 + churn analytics on Microsoft Fabric** — CRM · Marketing · Commerce,
> with a churn signal that is **derived from behaviour**, not invented.

![Fabric](https://img.shields.io/badge/Microsoft_Fabric-Lakehouse_+_Direct_Lake-purple?style=for-the-badge&logo=microsoft)
![Deploy](https://img.shields.io/badge/deploy-idempotent_(state.json)-blue?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-39_passing-brightgreen?style=for-the-badge)

**Workspace**: `CDR - Marketing Campaign`

---

## Why this project exists

Churn is the use case clients ask for most. It is also the one demos get wrong most often: a
`churn_risk_score` column is generated at random, nobody checks it, and the whole story collapses
the moment someone asks *"why is this customer at risk?"*.

This project takes the opposite approach.

| | Random-label approach | **This project** |
|---|---|---|
| Churn ↔ recency | r ≈ 0.01 | **r = +0.70** |
| Churn ↔ order count | r ≈ 0.01 | **r = −0.51** |
| Churn ↔ engagement | r ≈ 0.01 | **r = −0.46** |
| Mean score: churned vs active | flat (36 vs 37) | **55 vs 25** |
| Aggregates vs real orders | all zeros | computed, tested |
| Enforced by tests | ✗ | ✅ 34 tests |

The generator simulates behaviour first — sends, opens, clicks, unsubscribes, orders, support
tickets — and only then derives churn, CLV and lifecycle from it. The test suite fails the build if
that stops being true.

---

## The storyline

```
CAMP_007 "Black Friday Blast"
        │  over-mails (4× sends)
        ▼
   SEG_HIGH_VALUE  ──► unsubscribe spike ──► engagement halves ──► orders stop ──► churn
```

A retention campaign burns the segment it was meant to protect. **~51 % of the at-risk cohort
traces back to it**, so root-cause analysis genuinely finds the culprit rather than being handed
the answer.

Measured on the **deployed** semantic model — the culprit is unmistakable:

| Campaign | Sends / customer | Unsubscribes |
|---|---|---|
| **Black Friday Blast** | **3.83** | **439** |
| Cart Reminder | 1.00 | 13 |
| Members Only | 1.00 | 15 |
| Loyalty Boost | 1.00 | 17 |

Demo arc: **detect** (who is at risk) → **diagnose** (why — which campaign) → **quantify**
(revenue at risk, which VIPs) → **act** (suppress, throttle, win back).

---

## Data model — 15 tables

| Domain | Tables |
|---|---|
| **CRM** | `crm_accounts`, `crm_customers`, `crm_segments`, `crm_customer_segments`, `crm_interactions`, `crm_customer_profile` |
| **Marketing** | `marketing_campaigns`, `marketing_assets`, `marketing_audiences`, `marketing_sends`, `marketing_events` |
| **Commerce** | `products`, `orders`, `order_lines`, `returns` |
| **Text corpus** | `customer_knowledge_notes/*.txt`, `email_bodies/*.txt` |

`crm_customer_profile` is the churn table — every column in it is **computed**, never drawn:
`churn_risk_score`, `risk_band`, `clv_eur`, `days_since_last_order`, `orders_90d`,
`engagement_rate`, `unsubscribed`, …

### The churn model

`churn_risk_score` = weighted blend of behavioural signals (weights in `config.yaml`, must sum to 1):

| Signal | Weight | Meaning |
|---|---|---|
| Recency | 0.30 | days since last order |
| Frequency drop | 0.20 | orders last 90 d vs previous 90 d |
| Engagement decay | 0.20 | open rate vs their own baseline |
| NPS | 0.15 | detractor = worse |
| Unsubscribed | 0.10 | opted out of email |
| Support friction | 0.05 | unresolved negative interactions |

Bands: **Low** 0-39 · **Medium** 40-64 · **High** 65-84 · **Critical** 85-100 · **Prospect** (never ordered).

> Churn applies to **buyers only**. Someone who never ordered has a *conversion* problem, not a
> churn problem — mixing the two fills the remediation budget with people who were never customers.

---

## Quick start

```powershell
pip install -r requirements.txt

# 1. Configure
copy src\config.example.yaml src\config.yaml    # then set capacity_id / tenant_id

# 2. Generate the dataset (~1 min)
python src\generate_data.py

# 3. MANDATORY gate
python -m pytest tests\ -v --tb=short

# 4. Deploy (idempotent, resumable)
python src\deploy_all.py
#   or a subset:  python src\deploy_all.py workspace lakehouse
#   or resume:    python src\deploy_all.py --from semantic_model
```

The generator prints a storyline check so a run proves the signal exists:

```
Storyline check
   fatigued cohort (CAMP_007 burn) : 2,463 customers
   mean churn score, fatigued      :  38.0
   mean churn score, everyone else :  24.1
   customers at risk (>= 65)       : 981 / 12,000 (8.2%)
   share of at-risk explained by CAMP_007: 51%
```

---

## Project layout

```
src/config.yaml          — workspace, storyline, churn weights, volumes (single source of truth)
src/generate_data.py     — behaviour simulation + derived churn
src/helpers.py           — Fabric API auth, async polling, config/state
src/state.json           — deployment IDs (idempotent, gitignored)
tests/test_smoke.py      — offline gate, 34 tests
theme/                   — accessible Fluent-2 Power BI theme (WCAG / colour-blind checked)
docs/ARCHITECTURE.md     — full design
```

---

## Status

| Layer | State |
|---|---|
| Config-driven generator with real churn | ✅ validated (39 tests) |
| Test gate | ✅ 39 passing |
| Workspace + Lakehouse (15 CSV + 420 text files) | ✅ deployed |
| Delta tables + curated churn views | ✅ deployed (Spark notebook) |
| Semantic model (Direct Lake, 12 tables / 45 measures) | ✅ deployed, 7/7 DAX checks |
| Ontology + Graph (Customer 360) | ⏳ next |
| Dual-source Data Agent | ⏳ planned |
| Power BI report | ⏳ planned |
| Portal | ⏳ planned |

Validated on the deployed model: 12 000 customers · 36 508 orders · 4.96 M€ revenue · AOV 135.83 € ·
981 customers at risk (9.6 %) · 285 k€ revenue at risk · all five risk bands populated ·
lifecycle coherent (at_risk 71.7 > churned 64.6 > active 26.4, prospects unscored).

### Curated views (created by the setup notebook)

| View | Purpose |
|---|---|
| `v_churn_cohort` | the actionable at-risk customers with their drivers |
| `v_campaign_pressure` | sends per customer per campaign — exposes the over-mailing |

Built on the same idempotent pattern as the sister demos (`Publicis-Live-Event`,
`Network_Operations`): config-driven, `state.json`, resumable `deploy_all.py`, mandatory test gate.
