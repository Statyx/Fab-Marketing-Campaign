"""Gate for the leak guard itself — offline, no Fabric needed.

The guard is the only thing standing between this public repo and a real identifier, so it
gets the same treatment as the rest: a validator that cannot fail is worthless, and a
validator that fires on ordinary text gets disabled within a week.

Every rule therefore has BOTH:
  - a detection test (it catches the thing it exists for), and
  - a silence test (it stays quiet on text that merely looks similar).

The silence tests are not decoration. On a sister repo the guard fired on comments that
merely mentioned a forbidden pattern, and the fix was to weaken the guard. The two
arithmetic lines below are the shapes that would have done the same here.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / ".github" / "scripts" / "check_client_leak.py"


@pytest.fixture(scope="module")
def guard():
    assert GUARD.exists(), f"{GUARD} missing — the CI job would fail on a fresh clone"
    spec = importlib.util.spec_from_file_location("check_client_leak", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan(guard, line, denylist=()):
    return guard.scan_line("probe.txt", 1, line, denylist)


# ── The guard must not become the leak it prevents ──────────────
def test_the_guard_names_nobody():
    """Two sister repos list ten client names in the file meant to forbid them.

    A public deny-list is a concentrated client portfolio — a worse disclosure than the
    isolated mention it was written to catch. Every rule here matches a shape; the only
    name-based rule reads a secret from outside the repo.
    """
    text = GUARD.read_text(encoding="utf-8")
    # A literal customer name would have to sit inside a regex or a list of strings.
    # What must never appear is a bare alphabetic deny-list of proper nouns.
    assert "CLIENT_PATTERNS" not in text, "a hardcoded client list is the leak, not the guard"
    assert "CLIENT_DENYLIST" in text, "the name-based rule must come from outside the repo"


def test_the_denylist_is_not_tracked():
    """`.clientdeny` holds names; committing it would publish them."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".clientdeny" in gitignore


def test_a_missing_denylist_warns_instead_of_failing(guard, monkeypatch, capsys):
    """No secret on a fork or a first run must not turn into a red build."""
    monkeypatch.delenv("CLIENT_DENYLIST", raising=False)
    monkeypatch.setattr(guard, "ROOT", ROOT / "does-not-exist")
    entries, _ = guard._denylist()
    assert entries == ()


def test_the_denylist_rule_fires_when_the_secret_is_present(guard):
    assert _scan(guard, "a mention of Contoso Ltd", denylist=("contoso",))
    assert not _scan(guard, "a mention of Contoso Ltd", denylist=("fabrikam",))


# ── GUID allow-list ─────────────────────────────────────────────
def test_a_real_guid_is_caught(guard):
    found = _scan(guard, "published to report 7f3b21ce-9a44-4d51-8c02-1e6d5b7a90ff in prose")  # leak-probe
    assert any("allow-list" in f for f in found), found


def test_the_placeholder_guids_stay_silent(guard):
    """All-zeros and the generated task-flow ids are committed on purpose."""
    assert not _scan(guard, 'capacity_id: "00000000-0000-0000-0000-000000000000"')
    assert not _scan(guard, '"id": "c3600000-0000-4000-a000-000000000004",')


def test_the_committed_taskflow_ids_are_all_allowed(guard):
    """The generator emits 18 ids; every one of them must pass the allow-list."""
    flow = (ROOT / "taskflow" / "marketing_taskflow.json").read_text(encoding="utf-8")
    for lineno, line in enumerate(flow.splitlines(), start=1):
        assert not _scan(guard, line), f"taskflow line {lineno} tripped the guard"


ATTACHMENT_URL = "https://github.com/user-attachments/assets/62775a56-8db1-4e8c-83f1-f4fc42f137d4"


def test_a_github_attachment_url_stays_silent(guard):
    """A GitHub attachment id addresses an image GitHub already serves from this README.

    This is not a cosmetic exemption. Without it the rule fired on the README screenshots,
    and acting on that finding deleted three of them — the guard degrading the repo it is
    supposed to protect. A rule that costs content has to earn it.
    """
    assert not _scan(guard, f'<img width="2548" alt="image" src="{ATTACHMENT_URL}" />')


def test_the_attachment_exemption_covers_the_url_and_nothing_else(guard):
    """Scoped to the URL span, not to the line — otherwise one image tag would blind a
    whole line and a real id could ride along beside it."""
    found = _scan(guard, f'src="{ATTACHMENT_URL}" and report 7f3b21ce-9a44-4d51-8c02-1e6d5b7a90ff')  # leak-probe
    assert any("7f3b21ce" in f for f in found), found
    assert not any("62775a56" in f for f in found), found


def test_the_committed_readme_screenshots_survive(guard):
    """The three screenshots exist and the guard is silent on them.

    Counts real URLs (prefix + a 36-char id), not the bare prefix: the hygiene section
    quotes the prefix in prose, and a substring count would drift with the documentation.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    urls = re.findall(r"github\.com/user-attachments/assets/[0-9a-fA-F-]{36}", readme)
    assert len(urls) == 3, urls
    for lineno, line in enumerate(readme.splitlines(), start=1):
        if "user-attachments" in line:
            assert not _scan(guard, line), f"README line {lineno} tripped the guard"


# ── Fabric endpoint ─────────────────────────────────────────────
def test_a_real_sql_endpoint_is_caught(guard):
    found = _scan(guard, "x7qk3zv9m2rt6bd1ncf8.datawarehouse.fabric.microsoft.com")  # leak-probe
    assert any("Fabric endpoint" in f for f in found), found


def test_the_test_suite_stub_endpoint_stays_silent(guard):
    """tests/test_smoke.py deliberately uses a 4-character host — it is a stub."""
    assert not _scan(guard, '"lakehouse_sql_endpoint": "stub.datawarehouse.fabric.microsoft.com"')


# ── Personal paths ──────────────────────────────────────────────
def test_a_personal_path_is_caught(guard):
    assert _scan(guard, r"open(r'C:\Users\jdupont\Desktop\config.yaml')")  # leak-probe


def test_a_placeholder_path_stays_silent(guard):
    assert not _scan(guard, r"copy your file to C:\Users\<you>\project")
    assert not _scan(guard, "the runner checks out under /home/runner/work")


# ── Personal initials prefix — the delicate one ─────────────────
def test_an_initials_prefixed_workspace_name_is_caught(guard):
    for line in [
        'workspace_name: "XY - Something Nice"',  # leak-probe
        "# ABC - Something Nice — Configuration",  # leak-probe
        "**Workspace**: `XY - Something Nice`",  # leak-probe
        '_config_value("workspace_name", "XY - Something Nice")',  # leak-probe
    ]:
        found = _scan(guard, line)
        assert any("initials" in f for f in found), f"missed: {line}"


@pytest.mark.parametrize("line", [
    "Revenue - COGS - Operating Expenses",
    "# Revenue - COGS - Operating Expenses",
    "margin_eur = Revenue - COGS",
    "FAB_W = SLIDE_W - FAB_L - GAP - M",
    "CARD_H = ROW1_Y - CARD_Y - GAP",
    "churn = Recency - Frequency - Engagement",
])
def test_arithmetic_and_all_caps_chains_stay_silent(guard, line):
    """The shape `XX - Name` also describes a subtraction chain.  (leak-probe)

    Two discriminators keep these quiet: the initials must open the name (not sit
    mid-chain after " - "), and they are 2-3 letters, so a four-letter accounting
    acronym never qualifies. Weakening either one re-opens the false positives.
    """
    found = [f for f in _scan(guard, line) if "initials" in f]
    assert not found, f"false positive on: {line} -> {found}"


# ── Renamed repositories ────────────────────────────────────────
def test_the_old_repo_names_are_caught(guard):
    assert _scan(guard, "see Network_Operations for the pattern")  # leak-probe
    assert _scan(guard, "ported from The_AI_Skill_Analyzer")  # leak-probe


def test_the_client_prefixed_repo_name_is_caught_by_shape(guard):
    """The third old name carries a client, so it is matched by shape, never spelled out."""
    found = _scan(guard, "sister demo Acmecorp-Live-Event uses the same pattern")  # leak-probe
    assert any("old prefix" in f for f in found), found


def test_the_current_repo_names_stay_silent(guard):
    for line in [
        "sister demos (`Fab-Live-Event`, `Fab-Network-Operations`)",
        "On two sister projects (Fab-Live-Event, Fab-Network-Operations)",
        "Fab-Analyze-Data-Agent",
    ]:
        assert not _scan(guard, line), f"false positive on: {line}"


def test_the_probe_marker_is_inert_outside_the_guard(guard):
    """The per-line exemption is only safe if it cannot spread.

    A whole-file exemption (what the sister repos use) blinds the scanner to everything
    else in that file. A per-line marker is tighter, but it would become the same hole the
    moment someone dropped it on a real leak somewhere else — so the marker only means
    anything inside the guard and its own tests, and that set is pinned here.
    """
    assert guard.PROBE_FILES == frozenset(
        {".github/scripts/check_client_leak.py", "tests/test_leak_guard.py"}
    )
    marked = "workspace_name: 'ZQ - Something Nice'  # leak-probe"
    assert guard.is_probe_line("tests/test_leak_guard.py", marked)
    assert not guard.is_probe_line("docs/ARCHITECTURE.md", marked)
    assert not guard.is_probe_line("README.md", marked)


# ── The repo itself must be clean ───────────────────────────────
def test_the_tracked_tree_is_clean(guard):
    """The gate that would have caught the audit findings, run on the current tree."""
    findings = []
    for rel in guard.tracked_files():
        findings.extend(guard.scan_file(rel, ()))
    assert not findings, "tracked files leak:\n  " + "\n  ".join(findings)
