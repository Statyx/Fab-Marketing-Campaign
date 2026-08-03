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
- **One Fabric item has ONE owning generator.** Two sessions published to the same report
  id (`<REPORT_ID_REDACTED>` — real identifiers never belong in this file) and silently
  overwrote each other for a day. The fix that
  held was not a better guard, it was **a single owner**: `src/deploy_report.py`, and the second
  generator was deleted. `test_there_is_exactly_one_report_generator` fails if a `deploy_report*.py`
  reappears — adding one must be a decision, not something that shows up in a merge. If you do add
  one, give it a **distinct display name** *and* a **distinct `state.json` key**: sharing either is
  enough to collide, and a guard that only checks the *id* leaves a hole on a first run.
  **Deleting a generator can silently delete its tests.** The layout suite was written against the
  arc module, so removing the file would have taken the entire clipping protection with it while
  the gate stayed green. Port the tests onto the survivor *first*, and prove the refactor is
  output-neutral by hashing the built report before and after.
- **Data Agent must be DUAL-SOURCE**: ontology (GQL) for relationships/RCA/impact, semantic model
  (DAX) for every number. The Fabric IQ ontology TimeSeries/measure path returns empty for value
  questions — proven twice on sister projects. Route explicitly in `aiInstructions`.
- **Legacy PBIX only** for reports (`report.json` with `sections[].visualContainers[]`), never PBIR.
  Every visual needs a `prototypeQuery`. Multi-colour bars need the same column in Category AND Series.
- **Power BI clips text — it never shrinks the font and never warns.** A box too short for its
  font renders truncated glyphs on stage with a perfectly successful deploy. Never eyeball
  geometry — `validate_layout()` computes the fit and `sys.exit(1)`s before publishing.
  Two terms, kept separate: line height is **proportional**, padding/chrome is a **constant**.
  A single "px per pt" multiplier cannot express a constant and under-sizes small text.
  `min_height = pt * (96/72) * 1.35 + 8`  (1pt = 4/3 px @96 DPI, Segoe UI line box ~1.35, 8px pad).
  A `cardVisual` stacks **three** texts — title + callout value + category label — and **each keeps
  its own padding**: `min_height = sum(pt * 1.8 + 8) + 24`. The earlier model collapsed the three
  pads into one (`sum(pt) * 1.8 + 32`) and was **disproved by a render**: a 112px card holding
  11 + 24 + 9 pt computed 111.2, passed the validator, and shipped with its bottom label clipped on
  screen. That single observation now outranks the derivation, and it is locked by
  `test_the_card_stack_that_clipped_on_screen_is_rejected`.
  **The one constant with evidence behind it is that stack; 1.35, 8 and 24 are still calculated.**
  Re-check visually when a font size changes, and treat a passing validator as necessary, not
  sufficient.
  **`show: false` is a request, not a guarantee — Power BI ignored ours.** All 20 cards shipped
  with `objects.categoryLabel.show = false`; the definition read back from Fabric still carried it,
  and the renderer drew the label anyway, clipped, because the box had been sized for two texts.
  The validator was not wrong — it believed a declaration the engine did not honour. So **never let
  a hide toggle be what makes a box fit**: size for every text the visual declares, and treat the
  saved space as a bonus if the toggle happens to work. `test_a_card_fits_even_if_power_bi_ignores_show_false`
  computes the stack ignoring `show` entirely; `test_the_category_label_is_declared_visible` keeps
  the label on, because satisfying a requirement by relying on a renderer bug is not satisfying it.
  A validator must model what is **rendered**, not what is declared: read `show`, and treat an
  absent group as **visible** (that is Power BI's default). Inferring "hidden" from a missing
  `fontSize` under-reserves.
  Growing the card is then a **grid** change, not a card change: 112 → 128 pushed the cards from
  y=88..200 to y=88..216, so content row 1 had to move 208 → 224 and shrink 242 → 226 to keep its
  bottom at 450. Bind the call sites to `CARD_Y/CARD_H/ROW1_Y/ROW1_H/ROW2_Y/ROW2_H` rather than
  repeating literals 34 times — the constants existed while the call sites still hard-coded 88/112,
  which is exactly how a grid drifts from the model that validates it.
- Header bands are `z=0` decoration with the title/subtitle textboxes at `z=1` on top; the overlap
  check therefore only considers `z >= 1`. Two textboxes that overlap by 2px is a real defect.
- **Renaming a measure in a shared semantic model is a breaking change.** Fabric does not warn,
  the report keeps the old reference and fails only at render with "Something's wrong with one or
  more fields". Keep the old name as a hidden alias measure (`isHidden: true`) pointing at the new
  one, and check consumers before deploying.
- **Validate columns, not just measures.** `EVALUATE ROW("v",[M])` only exercises measures and lets
  broken column references through; test a column with `EVALUATE TOPN(1, VALUES('t'[c]))`.
  A broken column kills a visual just as hard.
- **Semantic model**: avoid ambiguous relationship paths (two routes between the same two tables) —
  the model import fails outright.
- **`COUNTROWS(FILTER(...))` returns BLANK, not 0** → wrap in `COALESCE(..., 0)`.
- **One shared semantic model, several generators → the push must be a UNION.** Each script
  defines the model in full, so pushing it replaces everything and whoever deploys last erases the
  other's measures. `carry_over_measures_reports_use()` in `deploy_semantic_model.py` reads the live
  model back and copies over any measure it doesn't define that a report still uses (hidden, DAX
  verbatim). Unused ones are still dropped.
- **`updateDefinition` can return 202/Succeeded while applying nothing** → always read the
  definition back and compare before declaring success.
- **A wrong `az` tenant does not look like an auth problem — it looks like a broken artefact.**
  `az` silently flips back to the corporate tenant. The token stays valid and lists 172 workspaces
  — of the *other* directory — so Fabric answers 404 EntityNotFound and the Power BI REST API
  answers **401 with an empty body**. That 401 reads as "expired token" and sends you diagnosing
  auth while the report is healthy: it once turned a passing report into a fake 0/35 and nearly
  got its content rewritten. Before believing any Fabric failure, check
  `az account show --query tenantId` against `config.yaml → tenant_id`.
  The guard is `helpers.ensure_tenant(cfg)`, called from **every** entrypoint's `main()`
  (`deploy_all`, `deploy_report`, `validate_report`) — there is a test that
  fails if a new one forgets. One shared implementation, so it cannot drift between scripts.
- Capacity pauses when idle → resume before deploy/demo.
- Never use `az rest` from a Python subprocess (hangs). Use `requests` + `az account get-access-token`.
- PowerShell `Set-Content -Encoding utf8` writes a **BOM** and breaks JSON parsing → use
  `[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding $false))`.
- PowerShell `Measure-Object -Line` counts **non-empty lines only** — an empty string is 0 lines.
  It under-reports any file with blank lines, whatever the source (`Get-Content`, a pipeline, a
  hand-made array). Proof: `@("a","","b") | Measure-Object -Line` → 2. For a real count use
  Python (`blob.count(b"\n")`).
- **A clean merge is not a correct merge.** Git guarantees no textual collision, not the absence of
  semantic redundancy. Merging two branches that independently learned the same lesson left
  `copilot-instructions.md` stating four rules twice, with **no conflict** — different words, same
  rule. After any merge that touches shared prose or tests, check for *duplicated meaning*, not just
  markers. For test files that means scanning every module-scope `def`, not just `def test_` — a
  duplicated fixture is worse than a duplicated test: pytest stays green and the surviving function
  runs under the expected name with the wrong body. (Fixtures are module-scoped, so the same name in
  two files is fine — unless a `conftest.py` exists, and then a local fixture silently shadows it.)
- **Any literal that lands in the tenant is shared state.** `description`, `displayFolder`,
  `aiInstructions` — if two generators word them differently, each alternating deploy flips them in
  the live artefact. Invisible in code review, visible only in Fabric, impossible to attribute after
  the fact. `aiInstructions` is the worst: it routes the Data Agent, so the agent's behaviour changes
  between deploys with no code change. Align the wording across generators, don't just merge it.
- Terminal PATH can be wiped by venv activation — restore from Machine+User env.

## Public repo — the guard must not become the leak
This repo is public. `.github/scripts/check_client_leak.py` runs in CI on every branch.

**Detect by shape, never by name.** Two sister repos guard against customer names by
listing those names in the guard itself — one plainly, one with a letter parenthesised so
a full-text search misses it and a human does not. A public file enumerating a client
portfolio is a worse disclosure than the single mention it was written to catch. Every
rule here matches a form: an unrecognised GUID, an opaque token in front of a Fabric
service domain, a personal filesystem path, initials-plus-name anchored at the start of a
display name, a renamed-repo shape. If a name-based check is genuinely needed, read it
from `CLIENT_DENYLIST` (Actions secret) or a gitignored `.clientdeny`, and **degrade to a
warning when absent — never a failure**.

**The GUID rule is an allow-list, and that is the point.** The sister rule only flags a
GUID preceded by an identity label (`tenant_id`, `client_id`). The real report id this
repo published sat in the middle of a prose sentence, so that rule would have walked past
it for 27 commits. An allow-list also never has to write the real identifier down in order
to catch it.

**Scan `git ls-files`, never the working tree** — `__pycache__/*.pyc` and `node_modules`
embed absolute paths and produce guaranteed false positives.

**Exempt lines, not files.** The guard and its tests must spell out fake leaks to prove
the rules fire. Exempting the whole file (what the sister repos do) blinds the scanner to
everything else it contains. The marker here is per-line **and inert outside the guard's
own two files** (`PROBE_FILES`), so documenting it cannot silence a line and spraying it
elsewhere does nothing.

**Text rules fire on prose, including your own.** The workspace-prefix rule caught a
sentence in the README that was *describing* the rule. Reword; do not exempt. A shape rule
that cannot fire on documentation is a shape rule with a hole in it.

**A finding is a hypothesis, not a verdict — and acting on a wrong one costs content.** The
allow-list flagged three `github.com/user-attachments/assets/<guid>` URLs in the README and I
deleted them, which silently removed three screenshots. Those ids address images GitHub was
already serving from this public repo: public by construction, unrelated to the tenant. A
deny-list would never have seen them; an allow-list sees *every* identifier, so the burden is
on the reader to classify each one before removing anything. **Ask what the identifier
addresses** — a Power BI report in a private tenant is a leak, a CDN asset behind a public
README is not. Symptom to watch for: a "fix" that deletes rendered content rather than
replacing a value. Exempt at the **span** of the URL, never at the line, or one image tag
blinds everything sitting next to it. Both directions are pinned by
`test_a_github_attachment_url_stays_silent` and
`test_the_attachment_exemption_covers_the_url_and_nothing_else`.

**Concatenated string literals split a line, and line-scoped rules do not span them.** The
first version of those tests wrapped the URL across two source lines; the guard then saw a
bare GUID with no URL around it and failed on the test file itself. Keep any literal a
line-based rule must match on **one physical line**.

## CI runs on Linux; this repo was written on Windows
Every `src/*.py` carried `import os, sys, winreg` at module scope for the PATH workaround.
`winreg` is Windows-only, and the test suite imports those modules directly — so on
`ubuntu-latest` the whole suite died at collection. **A Windows machine cannot reproduce
it**, and a fresh-clone rehearsal on Windows proves nothing about the runner. Import it
under `if sys.platform == "win32"` and make `_restore_path()` return early elsewhere;
`test_no_src_module_imports_winreg_unconditionally` reads the **AST**, not the text, so a
comment mentioning winreg is not a false positive.

Spoofing `sys.platform` to fake a Linux run does not work: third-party packages branch on
it and then call the real OS's stdlib. `"linux"` makes numpy call `os.uname()`, `"darwin"`
makes urllib import `_scproxy` — both absent on Windows, both failures about the
simulation rather than about this repo. Check the structure and the behaviour separately,
and say plainly that the Linux run itself is unverified until CI runs it.

**A skip is a test that did not run.** 28 tests were skipped whenever `data/raw/` was
missing — including the entire behaviour-before-labels gate. CI regenerates the dataset
from its seed and fails the job if the summary reports any skip.

## Never claim "verified"
Do not write that something works in docs, instructions or agent prompts unless a test output or a
trace proves it. A false "verified" makes everything downstream retry a path that cannot work.

**Scope your claim to the command you ran.** Four false statements in one thread shared one
mechanism: a true command, then a sentence broader than what it covered. `git ls-tree main` says
what main has — it says nothing about the other branch. Compare both sides in the same command
(`Compare-Object (git ls-tree -r A --name-only) (git ls-tree -r B --name-only)`), and name the ref:
`origin/main` and a local `main` can differ materially.
