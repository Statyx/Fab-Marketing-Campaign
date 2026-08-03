# Marketing Campaign — Customer 360 & Churn

> **Customer 360 + churn analytics on Microsoft Fabric** — CRM · Marketing · Commerce,
> with a churn signal that is **derived from behaviour**, not invented.

![Fabric](https://img.shields.io/badge/Microsoft_Fabric-Lakehouse_+_Direct_Lake-purple?style=for-the-badge&logo=microsoft)
![Deploy](https://img.shields.io/badge/deploy-idempotent_(state.json)-blue?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-54_passing-brightgreen?style=for-the-badge)




---

## Why this project exists

Churn is the use case clients ask for most. It is also the one demos get wrong most often: a
`churn_risk_score` column is generated at random, nobody checks it, and the whole story collapses
the moment someone asks *"why is this customer at risk?"*.

This project takes the opposite approach — and measures it. Figures below come from the shipped
dataset (buyers only, `seed=42`):

| | Random-label approach | **This project** |
|---|---|---|
| Churn ↔ days since last order | \|r\| < 0.02 | **r = +0.84** |
| Churn ↔ orders in last 90 d | \|r\| < 0.02 | **r = −0.56** |
| Churn ↔ CLV | \|r\| < 0.02 | **r = −0.43** |
| Churn ↔ engagement rate | \|r\| < 0.02 | **r = −0.39** |
| Mean score: lapsed > 180 d vs recent buyers | flat | **59.1 vs 19.9** |
| Mean score: unsubscribed vs subscribed | flat | **52.0 vs 28.6** |
| Aggregates vs real orders | all zeros | computed, reconciled |
| Enforced by tests | ✗ | ✅ 54 tests |

The generator simulates behaviour first — sends, opens, clicks, unsubscribes, orders, support
tickets — and only then derives churn, CLV and lifecycle from it. The test suite fails the build if
that stops being true.


<img width="2548" height="1266" alt="image" src="https://github.com/user-attachments/assets/62775a56-8db1-4e8c-83f1-f4fc42f137d4" />
<img width="2548" height="1266" alt="image" src="https://github.com/user-attachments/assets/8d0033cb-399a-4606-8992-63dcb1e8e338" />
<img width="1788" height="992" alt="image" src="https://github.com/user-attachments/assets/3389678e-07b1-4b97-91f2-31086261afab" />

---

## The storyline

```
CAMP_007 "Black Friday Blast"
        │  over-mails (≈4× sends)
        ▼
   SEG_HIGH_VALUE  ──► unsubscribe spike ──► engagement halves ──► orders stop ──► churn
```

A retention campaign burns the segment it was meant to protect. **≈48 % of the at-risk cohort
traces back to it**, so root-cause analysis genuinely finds the culprit rather than being handed
the answer — and there is a real residual cohort that churned for other reasons.

Measured on the shipped dataset — the culprit is unmistakable:

| Campaign | Sends / customer | Unsubscribes |
|---|---|---|
| **Black Friday Blast** | **3.90** | **247** |
| every other campaign | 1.00 | ≤ 28 |

The burned cohort (2 492 customers) is measurably worse than the rest: mean churn score
**33.9 vs 23.7**, mean engagement **0.121 vs 0.214**.

`storyline.fatigue_share` (0.62) is the dial: too low and the campaign is noise, too high and
"at risk" becomes a synonym for "received CAMP_007" and the diagnosis is tautological.

Demo arc: **detect** (who is at risk) → **diagnose** (why — which campaign) → **quantify**
(revenue at risk, which VIPs) → **act** (suppress, throttle, win back).

---

## Data model — 15 tables

| Domain | Tables |
|---|---|
| **CRM** | `crm_accounts`, `crm_customers`, `crm_segments`, `crm_customer_segments`, `crm_interactions`, `crm_customer_profile` |
| **Marketing** | `marketing_campaigns`, `marketing_assets`, `marketing_audiences`, `marketing_sends`, `marketing_events` |
| **Commerce** | `products`, `orders`, `order_lines`, `returns` |
| **Text corpus** | `customer_knowledge_notes/*.txt` (1 500), `email_bodies/*.txt` (20) |

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

Design details, filter-direction rules and inherited lessons: **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**.

---

## Quick start

```powershell
pip install -r requirements.txt

# 1. Configure
copy src\config.example.yaml src\config.yaml    # then set capacity_id / tenant_id / az_subscription

# 2. Generate the dataset (~1 min)
python src\generate_data.py

# 3. MANDATORY gate
python -m pytest tests\ -v --tb=short

# 4. Deploy (idempotent, resumable)
python src\deploy_all.py
#   or a subset:  python src\deploy_all.py workspace lakehouse
#   or resume:    python src\deploy_all.py --from semantic_model
#   or skip:      python src\deploy_all.py --skip ontology,graph

# 5. Run the portal (embedded report pages + Data Agent chat)
.\portal\start.ps1                              # http://localhost:8000
```

Deploy order is strict: `workspace → lakehouse → setup notebook → semantic model → ontology →
graph → report → data agent`.

The generator prints a storyline check so a run proves the signal exists:

```
Storyline check
   fatigued cohort (CAMP_007 burn) : 2,492 customers
   mean churn score, fatigued      :  33.9
   mean churn score, everyone else :  23.7
   mean engagement, fatigued       : 0.121
   mean engagement, everyone else  : 0.214
   customers at risk (>= 65)        : 825 / 12,000 (6.9%)
   share of at-risk explained by CAMP_007: 48%
```

---

## Project layout

```
src/config.yaml               — workspace, storyline, churn weights, volumes (single source of truth)
src/generate_data.py          — behaviour simulation + derived churn
src/helpers.py                — Fabric API auth, async polling, config/state
src/deploy_all.py             — orchestrator (strict order, resumable, tenant-guarded)
src/deploy_semantic_model.py  — Direct Lake model: 12 tables / 11 relationships / 50 measures
src/deploy_ontology.py        — 8 entities / 9 relationships (Fabric IQ)
src/deploy_graph.py           — graph definition + RefreshGraph
src/deploy_report.py          — Power BI report, 4 persona pages (legacy PBIX) + layout/field validators
src/validate_report.py        — replays every visual's prototypeQuery in DAX (proves none render blank)
src/build_taskflow.py         — generates the workspace task flow JSON from config.yaml
src/deploy_data_agent.py      — dual-source agent (ontology GQL + semantic model DAX)
src/state.json                — deployment IDs (idempotent, gitignored)
portal/                       — FastAPI portal: 4 personas, embedded report pages + Data Agent chat
tests/test_smoke.py           — offline gate: data signal, report ↔ model, portal ↔ report, layout, guards
tests/test_taskflow.py        — task flow gate (schema, DAG, config sync, dual-source)
taskflow/                     — workspace task flow + import instructions
theme/                        — accessible Fluent-2 Power BI theme (WCAG / colour-blind checked)
docs/ARCHITECTURE.md          — full design
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

Everything below was deployed **and read back from the tenant** on workspace
`CDR - Marketing Campaign`. Nothing here is claimed from a script's exit code alone.

| Layer | Code | Deployed |
|---|---|---|
| Config-driven generator with real churn | ✅ | n/a |
| Test gate (104 tests, fully offline) | ✅ | n/a |
| Workspace + Lakehouse (15 CSV + 420 text files) | ✅ | ✅ `LH_Customer360` |
| Delta tables + curated churn views (Spark notebook) | ✅ | ✅ `NB_Setup_Customer360` |
| Semantic model (Direct Lake, 12 tables / 50 measures) | ✅ | ✅ `SM_Marketing_Analytics` |
| Workspace task flow (6 tasks, generated + gated) | ✅ | ✅ imported |
| Ontology + Graph (Customer 360) | ✅ | ✅ `ONT_Customer360` |
| Power BI report (4 pages / 46 visuals) | ✅ | ✅ `RPT_Marketing_Churn`, 35/35 visual queries return data |
| Portal (4 personas, embed + chat) | ✅ | ✅ running, chat verified end-to-end |
| Dual-source Data Agent | ✅ | ✅ `Marketing_Churn_Agent` |

How each ✅ was proven:

- **Semantic model** — the definition is read back after every push and compared measure by
  measure against what was sent (50/50 match). See *Two deployment traps* below.
- **Report** — every measure and column referenced by its 46 visuals was resolved against the
  live model via `executeQueries`; zero broken bindings. `validate_report.py` additionally
  replays each visual's `prototypeQuery` in DAX: 35/35 return data, so none renders blank.
- **Data Agent** — a readback confirms both datasources were accepted:
  `ontology` → `ONT_Customer360`, `semantic_model` → `SM_Marketing_Analytics`.

Live figures returned by the deployed model (not from the local CSVs):

| Measure | Value |
|---|---|
| Total Customers | 12 000 |
| Buyers | 10 513 |
| Total Orders | 37 466 |
| Revenue | 5 083 349.74 € |
| Product Revenue | 5 083 349.74 € *(cross-check: order lines roll up to orders)* |
| Average Order Value | 135.68 € |
| Customers at Risk | 825 (7.85 % of buyers) |
| Avg Churn Score | 29.51 |
| Revenue at Risk | 235 196.07 € |
| CLV at Risk | 154 865.60 € |
| Total Sends | 72 799 |
| Total Events | 17 557 |
| Unsubscribed Customers | 499 |

Risk bands are all populated and the lifecycle ordering is coherent
(`at_risk` 71.5 > `churned` 64.3 > `active` 25.0, prospects unscored).

### Two deployment traps this repo now guards against

Both were hit for real on this tenant and both were **silent** — every script printed OK.

1. **`updateDefinition` can succeed and change nothing.** The call returns `202`, the operation
   polls to `Succeeded`, and the previous definition stays in place. The model sat one revision
   behind, so two report visuals were bound to measures that did not exist.
   `deploy_semantic_model.py` now reads the definition back, diffs the measure inventory, and
   re-pushes (up to 3×) before failing loudly.

2. **Direct Lake does not reframe on its own.** The setup notebook rewrites the Delta tables but
   the model keeps serving the previous snapshot, so the report and the Data Agent answer with
   stale numbers. `deploy_semantic_model.py` now forces a full refresh and waits for it.

A third, unrelated race: after deleting a notebook Fabric frees the *display name* later than it
removes the item from the listing, so recreating it returns `409 ItemDisplayNameNotAvailableYet`.
`notebook_utils.create_notebook()` retries on it.

The storyline is visible in the deployed data without being told: `Black Friday Blast` runs at
**3.90 sends per customer against 1.00** for the 19 other campaigns, and carries 247 unsubscribes.

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
