# Customer 360 & Churn — a Microsoft Fabric demo

A complete Customer 360 and churn analytics platform on Microsoft Fabric: CRM, marketing and
commerce in one Lakehouse, a Direct Lake semantic model, an ontology and knowledge graph, a
dual-source data agent, a Power BI report, and a conversational cockpit where four assistants
answer in natural language — and show where they looked.

![Fabric](https://img.shields.io/badge/Microsoft_Fabric-Lakehouse_+_Direct_Lake-purple?style=for-the-badge&logo=microsoft)
![Foundry](https://img.shields.io/badge/Microsoft_Foundry-supervisor_+_2_agents-orange?style=for-the-badge)
![Deploy](https://img.shields.io/badge/deploy-idempotent_(state.json)-blue?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-585_passing-brightgreen?style=for-the-badge)
[![CI](https://github.com/Statyx/Fab-Marketing-Campaign/actions/workflows/no-client-leak.yml/badge.svg)](https://github.com/Statyx/Fab-Marketing-Campaign/actions/workflows/no-client-leak.yml)

> **All data in this repository is synthetic**, generated from a seed by
> [`src/generate_data.py`](src/generate_data.py). No real customer, account or campaign appears
> anywhere. The workspace name, capacity and tenant are read from a gitignored
> `src/config.yaml` — see [`src/config.example.yaml`](src/config.example.yaml).

Deployment is one command and is idempotent — see [Quick start](#quick-start).
Design rationale lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); operational notes and
the hard-won traps in [`docs/ENGINEERING-NOTES.md`](docs/ENGINEERING-NOTES.md).

---

## Every month, customers leave — often in silence.

![The cockpit](docs/images/teaser-opening.png)

Churn is the use case clients ask for most. It is also the one demos get wrong most often: a
`churn_risk_score` column is generated at random, nobody checks it, and the whole story collapses
the moment someone asks *"why is this customer at risk?"*.

This project takes the opposite approach — and measures it. The generator simulates **behaviour
first** (sends, opens, clicks, unsubscribes, orders, support tickets) and only then derives churn,
CLV and lifecycle from that behaviour. Figures below come from the shipped dataset, buyers only,
`seed=42`:

| | Random-label approach | **This project** |
|---|---|---|
| Churn ↔ days since last order | \|r\| < 0.02 | **r = +0.84** |
| Churn ↔ orders in last 90 d | \|r\| < 0.02 | **r = −0.56** |
| Churn ↔ CLV | \|r\| < 0.02 | **r = −0.43** |
| Churn ↔ engagement rate | \|r\| < 0.02 | **r = −0.39** |
| Mean score: lapsed > 180 d vs recent buyers | flat | **59.1 vs 19.9** |
| Mean score: unsubscribed vs subscribed | flat | **52.0 vs 28.6** |
| Aggregates vs real orders | all zeros | computed, reconciled |
| Enforced by tests | ✗ | ✅ 585 tests |

The test suite fails the build if that stops being true — correlation floors, direction of effect,
band coverage, and aggregates matching the transactional truth are all gated.

🎬 Teaser: [`marketing/teaser-c360-en.mp4`](marketing/teaser-c360-en.mp4) (English) ·
[`marketing/teaser-c360.mp4`](marketing/teaser-c360.mp4) (French)

---

## Repository structure

| Folder | Theme | Contents |
|---|---|---|
| `src/` | **Fabric deployment** | Idempotent API scripts — workspace, lakehouse, semantic model, ontology, graph, report, data agent — plus the behaviour-first data generator |
| `app-v2/` | **The cockpit (V2)** | React/TypeScript static SPA hosted by Rayfin on a Fabric capacity; talks to Fabric and Foundry directly from the browser |
| `portal/` | **The portal (V1)** | FastAPI app, four personas, embedded report pages + data agent chat, `http://localhost:8000` |
| `taskflow/` | **Workspace task flow** | Generated task-flow JSON + import instructions, so the workspace reads as a journey |
| `theme/` | **Design** | Accessible Fluent-2 Power BI theme, WCAG and colour-blind checked |
| `tests/` | **The gate** | 585 offline tests — data signal, report ↔ model, layout, leak guard, task flow, supervisor |
| `docs/` | **Documentation** | Architecture, engineering notes, screenshots |
| `marketing/` | **Assets** | Teaser videos and the screenshots used here |
| `.github/` | **CI** | Client-leak guard + pytest, on every branch and PR |

Key files:

```
src/config.yaml               — workspace, storyline, churn weights, volumes (single source of truth)
src/state.json                — deployment IDs (idempotent, gitignored)
src/generate_data.py          — behaviour simulation, then derived churn
src/helpers.py                — Fabric API auth, async polling, config/state, tenant guard
src/deploy_all.py             — orchestrator (strict order, resumable, tenant-guarded)
src/deploy_semantic_model.py  — Direct Lake model: 12 tables / 11 relationships / 50 measures
src/deploy_ontology.py        — 8 entities / 9 relationships (Fabric IQ)
src/deploy_graph.py           — graph definition + RefreshGraph
src/deploy_report.py          — Power BI report, 4 persona pages (legacy PBIX) + layout/field validators
src/validate_report.py        — replays every visual's prototypeQuery in DAX (proves none renders blank)
src/build_taskflow.py         — generates the workspace task flow JSON from config.yaml
src/deploy_data_agent.py      — dual-source agent (ontology GQL + semantic model DAX)
src/deploy_supervisor_agent.py— Foundry supervisor over two subordinate agents
tests/test_smoke.py           — offline gate: data signal, report ↔ model, portal ↔ report, layout, guards
tests/test_leak_guard.py      — gates the leak guard itself: a detection AND a silence test per rule
tests/test_taskflow.py        — task flow gate (schema, DAG, config sync, dual-source)
```

---

## How it fits together

```mermaid
flowchart LR
    subgraph App["Consumption"]
        V2["app-v2<br/>static SPA · Rayfin"]
        PBI["Power BI report<br/>4 pages · 46 visuals"]
        V1["portal<br/>FastAPI · 4 personas"]
    end

    subgraph Foundry["Microsoft Foundry — orchestration only"]
        SUP["Marketing-Supervisor<br/>routes &amp; relays, never computes"]
        FD["Marketing-Churn-Front-Door"]
        VOC["Voice-Of-Customer"]
        CORPUS[("voc-marketing-churn<br/>customer verbatims")]
    end

    subgraph Fabric["Microsoft Fabric — semantics live here"]
        DA["Marketing_Churn_Agent<br/>picks DAX or GQL"]
        SM["SM_Marketing_Analytics<br/>semantic model · 50 measures"]
        ONT["ONT_Customer360<br/>8 entities · 9 relationships"]
        LH[("LH_Customer360<br/>15 Delta tables")]
    end

    V2 -->|"natural language"| SUP
    V2 -->|"DAX executeQueries ~1s"| SM
    SUP -->|A2A| FD
    SUP -->|A2A| VOC
    FD -->|MCP| DA
    VOC -->|file_search| CORPUS
    DA -->|DAX · every number| SM
    DA -->|GQL · every relationship| ONT
    SM -->|Direct Lake| LH
    ONT -->|Delta| LH
    PBI --> SM
    V1 --> SM
    V1 --> DA
```

Two rules hold this together, and they are enforced rather than documented:

- **The semantic model answers the numbers; the ontology answers the relationships.** The data
  agent routes explicitly. Asking the ontology for a total returns empty — proven twice on sister
  projects — so every figure comes from a tested DAX measure.
- **Foundry orchestrates, Fabric computes.** The supervisor relays figures verbatim with their
  scope and recomputes nothing. The cost is latency (40–60 s for a supervised answer, against
  ~1.3 s for a direct DAX call), and that trade was made deliberately.

---

## Screens

#### The cockpit

Four assistants, one dataset. Each opens on its own indicators, with the conversation on the right.
The KPI strip is read live from the semantic model, never from a local file.

![Cockpit home](docs/images/cockpit-home.png)

#### Detect · understand · act

The retention screen: who is at risk, what it is worth, and the distribution from critical risk
down to plain prospect. Note the card that states its own denominator — *buyers, not the whole
base* — because that distinction is the difference between 7.8 % and 6.9 %.

![Retention](docs/images/retention.png)

#### Ask the question — the agent shows where it looked

A single answer assembled from **two subordinates**: the figures from Fabric, the verbatims from
the customer corpus, with the provenance chips underneath. It also states what it *did not* find —
only one of the listed customers had a matching verbatim — instead of quietly implying full
coverage.

![A two-source answer](docs/images/answer-two-sources.png)

#### A chain of agents, verified in the tenant

The topology is derived from the deployment code, never drawn by hand, and every component is
queried live when the page opens — a local file cannot prove an item still exists.

![Deployed agent chain](docs/images/architecture-chain.png)

#### An ontology connects everything

Customers, campaigns, orders, support — 8 entities and 9 relationships, queryable in GQL, each
bound to its Delta table.

![Ontology graph](docs/images/ontology-graph.png)

#### The first portal — split view, question and report side by side

Before the V2 SPA there was a Python/FastAPI portal, still in the repo under `portal/`. It is worth
keeping in the picture for one screen in particular: the **split view**, where the question, the
query the agent actually generated, and the Power BI page sit next to each other.

<img width="2548" height="1266" alt="The V1 portal home" src="https://github.com/user-attachments/assets/62775a56-8db1-4e8c-83f1-f4fc42f137d4" />

Ask *"which segments do the at-risk customers share?"* and the answer arrives with an **Ontologie ·
GRAPHE · GQL** badge and a `Voir la requête GQL` toggle that reveals the generated `MATCH … RETURN`
— the routing is visible, not asserted. The report on the right is the same semantic model, so the
7.8 % on the card and the figures in the conversation cannot drift apart.

<img width="2548" height="1266" alt="Split view — chat and Power BI report" src="https://github.com/user-attachments/assets/8d0033cb-399a-4606-8992-63dcb1e8e338" />

The ontology is inspectable from the portal itself: the 8 entities with their backing Delta tables,
the 9 relationships with their names, and a button to open the item in Fabric.

<img width="1788" height="992" alt="The ontology, inspected from the portal" src="https://github.com/user-attachments/assets/3389678e-07b1-4b97-91f2-31086261afab" />

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
the answer — and a real residual cohort churned for other reasons.

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

## The data & semantic layer

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

**"At risk" has three legitimate readings**, and a question that does not choose is
under-specified rather than unstable: `risk_band = 'High'` → 800 · `churn_risk_score >= 65`
(High + Critical) → 825 · `lifecycle_stage = 'at_risk'` → 593. All three are correct. This is why
every question in the app names its table *and* its column.

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

The Foundry supervision plane is deliberately **not** a `deploy_all.py` step — a Foundry agent is
not a Fabric item. Deploy it separately with `src/deploy_supervisor_agent.py`; see
[`docs/ENGINEERING-NOTES.md`](docs/ENGINEERING-NOTES.md).

The generator prints a storyline check, so a run proves the signal exists before anything is
deployed:

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

> That 6.9 % is **825 of all 12 000 contacts**. The deployed model reports the same 825 as
> **7.85 % of the 10 513 buyers**, because churn is scoped to buyers. Same numerator, different
> denominator — the app labels which one it is showing, and so should you.

---

## Status

Everything below was deployed **and read back from the tenant**. Nothing here is claimed from a
script's exit code alone.

| Layer | Code | Deployed |
|---|---|---|
| Config-driven generator with real churn | ✅ | n/a |
| Test gate (585 tests, fully offline) | ✅ | n/a |
| Workspace + Lakehouse (15 CSV + 420 text files) | ✅ | ✅ `LH_Customer360` |
| Delta tables + curated churn views (Spark notebook) | ✅ | ✅ `NB_Setup_Customer360` |
| Semantic model (Direct Lake, 12 tables / 50 measures) | ✅ | ✅ `SM_Marketing_Analytics` |
| Workspace task flow (6 tasks, generated + gated) | ✅ | ✅ imported |
| Ontology + Graph (Customer 360) | ✅ | ✅ `ONT_Customer360` |
| Power BI report (4 pages / 46 visuals) | ✅ | ✅ `RPT_Marketing_Churn`, 35/35 visual queries return data |
| Portal (4 personas, embed + chat) | ✅ | ✅ running, chat verified end-to-end |
| Dual-source Data Agent | ✅ | ✅ `Marketing_Churn_Agent` |
| Foundry supervisor + 2 subordinates | ✅ | ✅ dual-source answers observed |

How each ✅ was proven:

- **Semantic model** — the definition is read back after every push and compared measure by
  measure against what was sent (50/50 match).
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

---

## Public-repo hygiene

This repo is public and the demo data is synthetic. Nothing tracked here may name a real
customer, a real workspace owner, or a real Fabric item.

```powershell
python .github\scripts\check_client_leak.py     # exit 0 = clean
```

The guard **names nobody** — every rule matches a *shape*, not a name, because a public file
enumerating a client portfolio would be a worse disclosure than the mention it was written to
catch. It runs in CI on every branch and PR, and `tests/test_leak_guard.py` gates the guard
itself: every rule has a detection test **and** a silence test.

Full rule table, the reasoning behind the GUID allow-list, and why one exemption is scoped to a
URL span rather than a line: [`docs/ENGINEERING-NOTES.md`](docs/ENGINEERING-NOTES.md#public-repo-hygiene).

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The design source of truth — model, filter directions, inherited lessons |
| [`docs/ENGINEERING-NOTES.md`](docs/ENGINEERING-NOTES.md) | Public-repo hygiene, the V1 portal, the Foundry plane, task flow, deployment traps |
| [`app-v2/README.md`](app-v2/README.md) | The V2 cockpit: hosting, auth, the three rules it enforces |
