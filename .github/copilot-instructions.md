# Copilot Instructions — Fab-Marketing-Campaign

## Mandatory Testing Gate
Before running ANY `deploy_*.py`, generator, or artifact script:
```bash
python -m pytest tests/ -v --tb=short
```
If ANY test fails → **STOP. Fix the code first. Do not proceed.**

## Project Context
- Python 3.12, Windows. Fabric API deploy scripts in `src/` (idempotent via `state.json`).
- **Customer 360 + churn** demo: CRM + Marketing + Commerce on Fabric.
  Lakehouse → Semantic Model (Direct Lake) → Ontology/Graph → dual-source Data Agent → Power BI.
- Workspace name + capacity_id + tenant_id come from `src/config.yaml` (copy `src/config.example.yaml`).
- Design source of truth: `docs/ARCHITECTURE.md`.

## The non-negotiable design rule
**Behaviour first, labels last.**
The generator simulates what each customer actually did (sends, opens, clicks, unsubscribes,
orders, support interactions) and only THEN derives `churn_risk_score`, `clv_eur` and
`lifecycle_stage` from that behaviour.

This exists because the predecessor project drew `churn_risk_score` from a distribution: it
correlated with **nothing** (|r| < 0.02 against every behavioural signal), the mean was flat across
lifecycle stages, and 5% of customers were simultaneously "critical risk" and NPS-9 promoters.
Every churn question was therefore unanswerable and no ML story was possible.

`tests/test_smoke.py` enforces this — correlation floors, direction of effect, band coverage,
aggregates matching the transactional truth. **Never weaken those tests to make data pass.**

## The storyline (must stay discoverable in the data)
Campaign **CAMP_007 "Black Friday Blast"** over-mails the **SEG_HIGH_VALUE** segment (4× sends)
→ unsubscribe spike → engagement halves → orders stop → churn.
Roughly half the at-risk cohort traces back to it, so RCA genuinely finds the cause instead of
being told the answer.

- Churn is scoped to **buyers only**. A contact who never ordered has a *conversion* problem, not a
  churn problem — they get `risk_band = "Prospect"` and score 0.
- Weights live in `config.yaml → churn_model.weights` and must sum to 1.0 (tested).

## Data split
- **Lakehouse** (Delta, 15 tables): CRM (accounts, customers, segments, customer_segments,
  interactions, customer_profile), Marketing (campaigns, assets, audiences, sends, events),
  Commerce (products, orders, order_lines, returns).
- **Text corpus**: customer_knowledge_notes + email_bodies (for AI transformations / RAG).
- The **Semantic Model** answers the numbers; the **Ontology/Graph** answers the relationships.

## Deploy order (strict)
workspace → lakehouse → setup notebook (CSV→Delta) → semantic model → ontology → graph →
report → data agent.
One command: `python deploy_all.py` (idempotent, tenant-guarded).

## Inherited lessons — do not relearn these
- **One Fabric item has ONE owning generator.** Two sessions published to report
  `ace677a4-02a7-4cbf-bc16-8b695fea3c7d` and silently overwrote each other for a day. Arbitration
  gave it to the main checkout's `src/deploy_report.py`; `RESERVED_REPORT_IDS` in this branch's
  generator enforces it mechanically (it forks to a distinct item instead of updating). Before
  publishing to any existing item, check who else deploys to it.
- **Data Agent must be DUAL-SOURCE**: ontology (GQL) for relationships/RCA/impact, semantic model
  (DAX) for every number. The Fabric IQ ontology TimeSeries/measure path returns empty for value
  questions — proven twice on sister projects. Route explicitly in `aiInstructions`.
- **Legacy PBIX only** for reports (`report.json` with `sections[].visualContainers[]`), never PBIR.
  Every visual needs a `prototypeQuery`. Multi-colour bars need the same column in Category AND Series.
- **Semantic model**: avoid ambiguous relationship paths (two routes between the same two tables) —
  the model import fails outright.
- **`COUNTROWS(FILTER(...))` returns BLANK, not 0** → wrap in `COALESCE(..., 0)`.
- **One shared semantic model, several generators → the push must be a UNION.** Each script
  defines the model in full, so pushing it replaces everything and whoever deploys last erases the
  other's measures — the report then fails only at render with "Something's wrong with one or more
  fields". `carry_over_measures_reports_use()` in `deploy_semantic_model.py` reads the live model
  back and copies over any measure it doesn't define that a report still uses (hidden, DAX
  verbatim). Unused ones are still dropped. Renaming stays: new name + old name as `isHidden` alias.
- **Validate columns, not just measures.** `EVALUATE ROW("v",[M])` tests measures;
  `EVALUATE TOPN(1, VALUES('t'[c]))` tests columns. A broken column kills a visual just as hard.
- **Power BI clips text — it never shrinks the font and never warns.** Two terms, kept separate:
  line height is proportional (`pt * 1.8`), padding/chrome is **constant** (~8px textbox, ~32px
  card). A single "px per pt" multiplier cannot express a constant and under-sizes small text.
  These constants are **derived, never measured** against the renderer — do not restate them as
  verified. A **card stacks three texts** (title + calloutValue + categoryLabel) — sizing it
  against the callout alone clips the label. `validate_layout()` blocks the deploy on clipping,
  card stacks, overlap (only at `z >= 1`; the header band is a deliberate `z = 0` background)
  and out-of-page.
- **`updateDefinition` can return 202/Succeeded while applying nothing** → always read the
  definition back and compare before declaring success.
- Capacity pauses when idle → resume before deploy/demo.
- Never use `az rest` from a Python subprocess (hangs). Use `requests` + `az account get-access-token`.
- PowerShell `Set-Content -Encoding utf8` writes a **BOM** and breaks JSON parsing → use
  `[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding $false))`.
- Terminal PATH can be wiped by venv activation — restore from Machine+User env.

## Never claim "verified"
Do not write that something works in docs, instructions or agent prompts unless a test output or a
trace proves it. A false "verified" makes everything downstream retry a path that cannot work.
