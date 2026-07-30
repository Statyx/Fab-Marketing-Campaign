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

# 5. Run the portal (embedded report pages + Data Agent chat)
.\portal\start.ps1                              # http://localhost:8000
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
src/deploy_report.py     — Power BI report, 4 persona pages (legacy PBIX format)
src/validate_report.py   — replays every visual's prototypeQuery in DAX (proves none render blank)
src/build_taskflow.py    — generates the workspace task flow JSON from config.yaml
src/state.json           — deployment IDs (idempotent, gitignored)
portal/                  — FastAPI portal: 4 personas, embedded report pages + Data Agent chat
tests/test_smoke.py      — offline gate: data signal, report ↔ model, portal ↔ report contracts
tests/test_taskflow.py   — task flow gate (schema, DAG, config sync, dual-source)
taskflow/                — workspace task flow + import instructions
theme/                   — accessible Fluent-2 Power BI theme (WCAG / colour-blind checked)
```

---

## The portal

A FastAPI app that puts one **persona** in front of each report page: the page is embedded on
the right, a chat with the Data Agent on the left, both sharing the same accent colour.

```powershell
az login                  # the backend uses AzureCliCredential — no service principal
.\portal\start.ps1        # http://localhost:8000
```

`start.ps1` refuses to launch if `src/state.json` has no `workspace_id` / `report_id` /
`data_agent_id`, so a missing deploy surfaces as an error instead of an empty embed panel.

| Persona | Page | Question it answers |
|---|---|---|
| 🎯 Direction | Direction | how much value is exposed |
| 🛟 Retention | Retention | who is leaving, and which customers to call |
| 📣 Marketing | Marketing | which campaign caused it |
| 🛒 Commerce | Commerce | what it costs in revenue |

Everything is config-driven: personas live in the `AGENTS` dict in `portal/backend/main.py`,
the frontend discovers them from `/api/agents`, and all IDs are read from `src/state.json` —
there is not a single hardcoded GUID. Adding a persona is one dict entry.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | liveness + token expiry — hit this first on a 502 |
| `GET /api/agents` | persona registry + tenant/workspace context |
| `POST /api/agents/{key}/chat` | question → Data Agent, returns answer, tool trace, follow-ups |
| `GET /api/embed-token` | report embed URL + user token (user-owns-data) |
| `POST /api/admin/refresh-tokens` | force a token refresh without restarting |


---

## Workspace task flow

The workspace canvas that turns a flat item list into the story:

```
Ingest ──► Lakehouse ─┬─► Ontology (+ graph) ─┐
(notebook)            │                       ├─► Data Agent
                      └─► Semantic Model ─────┘
                              │
                              └──────────────────► Report
```

Both the semantic model **and** the ontology's graph feed the Data Agent — the dual-source rule
made visible on the canvas. The graph gets no task of its own (it is underlying to the ontology),
and the CSV→Delta notebook sits on the ingest task rather than duplicating it.

Fabric has **no public REST API for task flows**, so this is a generated JSON you import once:

```powershell
python src\build_taskflow.py     # -> taskflow\marketing_taskflow.json
```

Then workspace → task flow details pane → **Import and export task flow** → *Import*, and
replay the item assignments (the file cannot carry them). Full steps and the item→task table:
[`taskflow/README.md`](taskflow/README.md).

---

## Status

| Layer | State |
|---|---|
| Config-driven generator with real churn | ✅ validated |
| Test gate | ✅ 72 passing |
| Workspace + Lakehouse (15 CSV + 420 text files) | ✅ deployed |
| Delta tables + curated churn views | ✅ deployed (Spark notebook) |
| Semantic model (Direct Lake, 12 tables / 48 measures) | ✅ deployed |
| Workspace task flow (6 tasks, generated + gated) | ✅ imported |
| Ontology + Graph (Customer 360) | ✅ deployed |
| Dual-source Data Agent | ✅ deployed |
| Power BI report (4 pages / 46 visuals) | ✅ deployed, 35/35 visual queries return data |
| Portal (4 personas, embed + chat) | ✅ running, chat verified end-to-end |

Measured on the **deployed** model (`validate_report.py` + live Data Agent calls):
12 000 customers · 37 466 orders · 5.08 M€ revenue · AOV 135.68 € · 825 customers at risk (8 %) ·
154 866 € CLV at risk · NPS 7.98 · `Black Friday Blast` at **3.90 sends per customer vs 1.00**
for the 19 other campaigns, 247 unsubscribes — the culprit is visible without being told.

> ⚠️ The local `data/raw` CSVs are a **different draw** than what is in the Lakehouse
> (981 at risk / 4.96 M€ locally). Re-running `generate_data.py` + the setup notebook will
> move the report and the agent onto the local figures. Regenerate both together, never one alone.


### Curated views (created by the setup notebook)

| View | Purpose |
|---|---|
| `v_churn_cohort` | the actionable at-risk customers with their drivers |
| `v_campaign_pressure` | sends per customer per campaign — exposes the over-mailing |

Built on the same idempotent pattern as the sister demos (`Publicis-Live-Event`,
`Network_Operations`): config-driven, `state.json`, resumable `deploy_all.py`, mandatory test gate.
