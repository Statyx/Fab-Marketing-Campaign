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
- **Data Agent must be DUAL-SOURCE**: ontology (GQL) for relationships/RCA/impact, semantic model
  (DAX) for every number. The Fabric IQ ontology TimeSeries/measure path returns empty for value
  questions — proven twice on sister projects. Route explicitly in `aiInstructions`.
- **Legacy PBIX only** for reports (`report.json` with `sections[].visualContainers[]`), never PBIR.
  Every visual needs a `prototypeQuery`. Multi-colour bars need the same column in Category AND Series.
- **Power BI clips text, it never shrinks it and never warns.** A box too short for its font
  renders truncated glyphs on stage with a perfectly successful deploy. Never eyeball geometry —
  `deploy_report.validate_layout()` computes the fit and `sys.exit(1)`s before publishing:
  `min_height = pt * (96/72) * 1.35 + 8`  (1pt = 4/3 px @96 DPI, Segoe UI line box ~1.35, 8px padding).
  A `cardVisual` stacks **three** texts — title + callout value + category label — and the padding
  is **per container, not per line**: the three per-block pads collapse to one, then the card's own
  chrome (~24px) is added on top. So `min_height = sum(pt) * 1.8 + 8 + 24`, i.e. `sum(pt) * 1.8 + 32`
  — mirrored in the code as `… - 2 * TEXT_PAD + CARD_CHROME`. Do not write it as `+ 24`: that
  under-reserves by 8px. This is the one that bites: a 112px card holding 11 + 30 + 9 pt needs
  122px and silently clips its label. Shrink the callout font before growing the card, the row
  grid rarely has 10 spare pixels.
  **These constants (1.35, 8, 24) are calculated, never measured against the Power BI renderer.**
  The only evidence they hold is a human looking at a rendered page. Treat them as a working
  approximation, not a verified fact, and re-check visually when a font size changes.
- Header bands are `z=0` decoration with the title/subtitle textboxes at `z=1` on top; the overlap
  check therefore only considers `z >= 1`. Two textboxes that overlap by 2px is a real defect.
- **Renaming a measure in a shared semantic model is a breaking change.** Fabric does not warn,
  the report keeps the old reference and fails only at render with "Something's wrong with one or
  more fields". Keep the old name as a hidden alias measure (`isHidden: true`) pointing at the new
  one, and check consumers before deploying.
- Test a **column** with `EVALUATE TOPN(1, VALUES('table'[col]))`, not with `ROW()` — `ROW()` only
  exercises measures and lets broken column references through.
- **Semantic model**: avoid ambiguous relationship paths (two routes between the same two tables) —
  the model import fails outright.
- **`COUNTROWS(FILTER(...))` returns BLANK, not 0** → wrap in `COALESCE(..., 0)`.
- Capacity pauses when idle → resume before deploy/demo.
- Never use `az rest` from a Python subprocess (hangs). Use `requests` + `az account get-access-token`.
- PowerShell `Set-Content -Encoding utf8` writes a **BOM** and breaks JSON parsing → use
  `[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding $false))`.
- Terminal PATH can be wiped by venv activation — restore from Machine+User env.

## Never claim "verified"
Do not write that something works in docs, instructions or agent prompts unless a test output or a
trace proves it. A false "verified" makes everything downstream retry a path that cannot work.
