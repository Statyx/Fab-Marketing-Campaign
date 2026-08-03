#!/usr/bin/env python3
"""Fail if a real identifier or a personal marker leaks into Git-tracked files.

This repo is public and ships a *synthetic* Customer 360 / churn demo. It must never
contain real Fabric identifiers, real workspace SQL endpoints, personal filesystem paths,
or the name of whoever ran the demo.

WHY THIS FILE NAMES NOBODY
    Two sister repos guard against customer names by listing those names in the guard
    itself — one in a Python deny-list, one in a shell pattern with a letter parenthesised
    (`p(u)blicis`), which fools a full-text search and nobody else. A public file that
    enumerates a client portfolio is a worse leak than the isolated mention it was written
    to catch. So every rule here matches a **shape**, never a name. The only name-based
    rule reads an environment variable fed by a GitHub Actions secret, and is skipped with
    a warning when that secret is absent.

WHY ONLY git ls-files
    The working tree is deliberately NOT walked: `__pycache__/*.pyc` and `node_modules`
    embed absolute build paths and would produce false positives.

WHY THE GUID RULE IS AN ALLOW-LIST
    The sister scanners only flag GUIDs that follow an identity label (`tenant_id`,
    `client_id`). That rule would have missed this repo's worst leak: a real Power BI
    report id sitting in the middle of a prose sentence, with no label anywhere near it.
    Here **every** GUID is a finding unless it matches a known placeholder shape — which
    also means the real identifier never has to be written down in order to be caught.

Run locally:  python .github/scripts/check_client_leak.py
Exit code 0 = clean, 1 = leak found.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BINARY_SUFFIXES = {
    ".pptx", ".ppt", ".docx", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".zip", ".gz", ".parquet", ".woff", ".woff2", ".ttf", ".eot",
}

GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# The only GUID shapes this repo is allowed to commit:
#   - the all-zeros structural placeholder (config.example.yaml, deploy_graph.py, stubs)
#   - the deterministic task-flow ids built by _gid() in src/build_taskflow.py
#     ("c360" = Customer 360, sequential, v4-shaped). They are generated, not observed.
ALLOWED_GUID_RE = re.compile(
    r"^(?:0{8}-0{4}-0{4}-0{4}-0{12}|c3600000-0000-4000-a000-[0-9]{12})$",
    re.IGNORECASE,
)

# A GitHub attachment id is not an identifier of anything in the tenant: it addresses an
# image GitHub itself is already serving publicly from this repo's README. Flagging it was
# not a harmless false positive — acting on it silently deleted three screenshots, so the
# guard degraded the very repo it protects. A rule that costs content has to earn it.
ATTACHMENT_URL_RE = re.compile(
    r"https://github\.com/user-attachments/assets/"
    r"[0-9a-fA-F-]{36}",
)

# A real Fabric SQL / warehouse endpoint: a long opaque token followed by the service
# domain. `stub.datawarehouse.fabric.microsoft.com` is 4 characters and stays silent.
FABRIC_ENDPOINT_RE = re.compile(
    r"\b[a-z0-9]{20,}(?:-[a-z0-9]{20,})?\."
    r"(?:datawarehouse|dev\.azuresynapse|pbidedicated)\b",
    re.IGNORECASE,
)

# Personal home directories. Placeholders such as C:\Users\<you> are fine.
PERSONAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\Users\\|/Users/|/home/)"
    r"(?!<|\{|\$|%|USERNAME\b|username\b|you\b|user\b|runner\b)"
    r"[A-Za-z0-9._-]+"
)

# A personal workspace prefix: someone's initials in front of a display name, e.g. a
# workspace called "XY - Something Nice". Publishing it publishes who ran the demo.  (leak-probe)
#
# Two discriminators keep this quiet on arithmetic and on all-caps chains:
#   1. ANCHORED at the start of a name — line start, or just after a quote, backtick or
#      opening bracket, with an optional comment / markdown marker. So the `COGS` in
#      `Revenue - COGS - Operating Expenses` is skipped: it sits mid-chain, after " - ".
#   2. Initials are 2-3 letters, so a 4-letter accounting acronym like COGS or EBIT never
#      qualifies, and the tail must be Titlecase, so `GAP - M` in
#      `FAB_W = SLIDE_W - FAB_L - GAP - M` never qualifies either.
#
# Residual, accepted on purpose: a quoted label that genuinely starts with a 2-3 letter
# acronym followed by " - Titlecase" (e.g. "ROI - Analysis") will be flagged. Failing  (leak-probe)
# closed is the intended direction; reword it or move it out of the leading position.
OWNER_PREFIX_RE = re.compile(
    r"""(?:^|[`"'\[(])\s*
        (?:\#+\s*|//\s*|\*+\s*|--\s*)?
        (?P<initials>[A-Z]{2,3})\ -\ [A-Z][a-z]""",
    re.VERBOSE | re.MULTILINE,
)

# Repositories that were renamed. Referring to them from a public repo 404s, and one of
# the old names carried a customer name — so that one is matched by SHAPE (any prefix in
# front of "-Live-Event" other than the current "Fab-") and is never spelled out here.
LEGACY_REPO_RE = re.compile(r"\bNetwork_Operations\b|\bThe_AI_Skill_Analyzer\b")
LEGACY_LIVE_EVENT_RE = re.compile(r"\b(?!Fab-)[A-Z][A-Za-z]{2,}-Live-Event\b")

# The guard and its tests must spell out fake leaks in order to prove the rules fire. The
# sister repos solve this by exempting whole files, which blinds the scanner to anything
# else those files contain. This is tighter on two axes: the exemption is per LINE and
# visible in the diff, and the marker is INERT everywhere except the two files below — so
# writing about it in documentation cannot silence a line, and spraying it on a real leak
# elsewhere does nothing.
PROBE_MARKER = "leak-probe"
PROBE_FILES = frozenset(
    {".github/scripts/check_client_leak.py", "tests/test_leak_guard.py"}
)


def is_probe_line(rel: str, line: str) -> bool:
    """True when this exact line is an intentional fixture inside the guard itself."""
    return rel in PROBE_FILES and PROBE_MARKER in line


def _denylist() -> tuple:
    """Name-based rule, fed from outside the repo. Never hardcode a name here.

    Sources, in order: the CLIENT_DENYLIST environment variable (wired to a GitHub
    Actions secret in the workflow), then a local `.clientdeny` file (gitignored).
    One entry per line. Absent => the rule is skipped with a warning, never a failure.
    """
    raw = os.environ.get("CLIENT_DENYLIST", "")
    source = "CLIENT_DENYLIST"
    if not raw.strip():
        local = ROOT / ".clientdeny"
        if local.is_file():
            raw = local.read_text(encoding="utf-8")
            source = ".clientdeny"
    entries = tuple(
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return entries, source


def tracked_files() -> list:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.split("\0") if p]


def scan_line(rel: str, lineno: int, line: str, denylist: tuple) -> list:
    findings = []

    attachments = [m.span() for m in ATTACHMENT_URL_RE.finditer(line)]
    for match in GUID_RE.finditer(line):
        if ALLOWED_GUID_RE.match(match.group(0)):
            continue
        start, end = match.span()
        if any(a <= start and end <= b for a, b in attachments):
            continue
        findings.append(
            f"{rel}:{lineno}: GUID outside the placeholder allow-list: {match.group(0)}"
        )

    for match in FABRIC_ENDPOINT_RE.finditer(line):
        findings.append(f"{rel}:{lineno}: real Fabric endpoint: {match.group(0)}")

    for match in PERSONAL_PATH_RE.finditer(line):
        findings.append(f"{rel}:{lineno}: personal filesystem path: {match.group(0)}")

    for match in OWNER_PREFIX_RE.finditer(line):
        findings.append(
            f"{rel}:{lineno}: personal initials prefix in a display name: "
            f"{match.group('initials')} - ... (keep public names neutral)"
        )

    for match in LEGACY_REPO_RE.finditer(line):
        findings.append(f"{rel}:{lineno}: renamed repository: {match.group(0)}")

    for match in LEGACY_LIVE_EVENT_RE.finditer(line):
        findings.append(
            f"{rel}:{lineno}: renamed repository (old prefix): {match.group(0)}"
        )

    lowered = line.lower()
    for entry in denylist:
        if entry in lowered:
            findings.append(f"{rel}:{lineno}: denylisted literal (see CLIENT_DENYLIST)")

    return findings


def scan_file(rel: str, denylist: tuple) -> list:
    path = ROOT / rel
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if is_probe_line(rel, line):
            continue
        findings.extend(scan_line(rel, lineno, line, denylist))
    return findings


def main() -> int:
    denylist, source = _denylist()
    if not denylist:
        print(
            "WARNING: no CLIENT_DENYLIST secret and no .clientdeny file — the "
            "name-based rule is skipped. Shape-based rules still apply."
        )
    else:
        print(f"Name-based rule active: {len(denylist)} entries from {source}.")

    findings = []
    for rel in tracked_files():
        findings.extend(scan_file(rel, denylist))

    if findings:
        print("\nLeak check FAILED:\n")
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nReplace real values with visibly fake placeholders "
            "(e.g. <YOUR_WORKSPACE_ID> or 00000000-0000-0000-0000-000000000000)."
        )
        return 1

    print("Leak check passed: no real identifier, no personal marker in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
