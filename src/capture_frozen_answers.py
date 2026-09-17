"""Capture real supervisor answers so the demo's first clicks are instant.

Why this exists
---------------
A supervised answer costs 40-160 seconds, because routing happens on the data side and that is
deliberate. It is the right trade for a real question and the wrong one for the first click of a
demo, where a minute of spinner is a minute of dead air.

So the opening questions are answered from a recording. The rule that makes that acceptable, and
the reason this file is a capture script rather than a fixture someone types:

    **Nothing here is written by hand.** Every answer below came out of the live supervisor, with
    the tools it really called and the time it really took. A hand-written answer would look
    exactly as sourced as a real one on screen -- same prose, same provenance block, same routing
    badges -- while being a fabrication. That is strictly worse than no answer at all, because the
    audience cannot tell.

The app says so too: a replayed answer carries a "réponse enregistrée" chip with the capture date.
An undisclosed cache would quietly break the same contract the muted trace dot protects.

Staleness
---------
These answers are only as true as the data they were drawn from. `capturedAt` is stamped per
answer and shown in the UI, so a figure that has drifted is attributable rather than mysterious.
Re-run this script after any change to the Lakehouse, the semantic model or the agent prompts.

Partial runs are useful
-----------------------
The file is written after every answer. A run that dies at question 15 leaves 14 usable
recordings, and the app falls through to a live call for anything it does not hold -- so a
partial capture degrades to the current behaviour rather than to a wrong answer.

    python src/capture_frozen_answers.py            # capture what is missing
    python src/capture_frozen_answers.py --force    # re-capture everything
    python src/capture_frozen_answers.py --only direction
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deploy_supervisor_agent import supervisor_config  # noqa: E402
from deploy_foundry_agent import project_client  # noqa: E402
from helpers import (ensure_tenant, get_sdk_credential, load_config, load_state,
                     output_path, profile_dir)  # noqa: E402
from verify_supervisor import _items, _tool_names  # noqa: E402

APP = Path(__file__).parent.parent / "app-v2" / "src" / "data"
QUESTIONS = APP / "frozen-questions.generated.json"
ANSWERS = output_path(APP / "frozen-answers.generated.json", "captures", "frozen_answers.json")

# Same discriminator as the browser client (`app-v2/src/services/foundry.ts`): a subordinate
# dying mid-A2A is reported as HTTP 400, so the STATUS is useless and the code is the only
# signal. Measured at roughly one run in five on the RCA question.
TRANSIENT = ("tool_user_error", "server_error", "rate_limit_exceeded", "timeout",
             "429", "500", "502", "503", "504")


def is_transient(err: Exception) -> bool:
    blob = f"{type(err).__name__} {err}".lower()
    return any(c in blob for c in TRANSIENT)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_answers(payload: dict) -> None:
    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    with open(ANSWERS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def capture_context(cfg, state, fnd):
    required = ("workspace_id", "semantic_model_id", "data_agent_id",
                "foundry_supervisor_agent_name")
    missing = [key for key in required if not state.get(key)]
    if missing:
        raise RuntimeError(f"Deploy the profile before capturing answers; missing state: {missing}")
    if state["foundry_supervisor_agent_name"] != fnd["supervisor_agent_name"]:
        raise RuntimeError("Recorded supervisor does not match the selected configuration")
    return {
        "tenantId": cfg["tenant_id"], "workspaceId": state["workspace_id"],
        "semanticModelId": state["semantic_model_id"], "dataAgentId": state["data_agent_id"],
        "projectEndpoint": fnd["project_endpoint"], "agentName": fnd["supervisor_agent_name"],
        "model": fnd["model_deployment"],
    }


def ask(client, agent: str, model: str, question: str, attempts: int = 3):
    """One question, retried on a hop that died in flight. Returns (text, tools, seconds)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            oai = client.get_openai_client(agent_name=agent)
            t0 = time.time()
            resp = oai.responses.create(model=model, input=question)
            items = _items(resp)
            return (getattr(resp, "output_text", "") or "",
                    _tool_names(items),
                    round(time.time() - t0, 1))
        except Exception as e:  # noqa: BLE001 - the SDK raises several unrelated types here
            last = e
            if not is_transient(e) or attempt == attempts:
                break
            print(f"        transient ({type(e).__name__}) - retry {attempt + 1}/{attempts}")
            time.sleep(5)
    raise last  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-capture questions already recorded")
    ap.add_argument("--only", help="limit to one persona key")
    ap.add_argument("--depth", type=int, default=2, help="1 = openers only, 2 = + follow-ups")
    args = ap.parse_args()

    cfg = load_config()
    # az silently flips back to the corporate tenant, and the resulting failure reads as a broken
    # artefact rather than as an auth fault. Every entrypoint pins it first.
    ensure_tenant(cfg)

    if not QUESTIONS.exists():
        print(f"!! {QUESTIONS.name} is missing.")
        print("   Run it first, so the list stays derived from the registry rather than typed:")
        print("   cd app-v2 && npx tsx scripts/freeze-questions.ts")
        return 1

    entries = [e for e in load_json(QUESTIONS, {}).get("entries", [])
               if e.get("depth", 1) <= args.depth
               and (not args.only or e.get("persona") == args.only)]

    payload = load_json(ANSWERS, {"generatedBy": "src/capture_frozen_answers.py", "answers": {}})
    payload.setdefault("answers", {})
    answers: dict = payload["answers"]

    fnd = supervisor_config(cfg)
    agent, model = fnd["supervisor_agent_name"], fnd["model_deployment"]
    if profile_dir() is not None:
        context = capture_context(cfg, load_state(), fnd)
        if ANSWERS.exists() and payload.get("deployment") != context:
            raise RuntimeError("Existing captures are not from this profile's deployment; "
                               "archive them explicitly before capturing")
        payload["deployment"] = context

    todo = [e for e in entries if args.force or e["q"] not in answers]
    print("=" * 74)
    print(f"CAPTURE -- {len(todo)} question(s) to record on {agent} (of {len(entries)} selected)")
    print("           each costs 40-160s; the file is written after every answer")
    print("=" * 74)
    if not todo:
        print("\nNothing to do. Use --force to re-record.")
        return 0

    client = project_client(fnd, get_sdk_credential(process_timeout=90))

    ok = 0
    for i, e in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {e['persona']:<10} {e['q'][:82]}")
        try:
            text, tools, secs = ask(client, agent, model, e["q"])
        except Exception as exc:  # noqa: BLE001
            print(f"        FAILED after retries: {type(exc).__name__}: {str(exc)[:200]}")
            continue

        # An answer with no tool call is the app's own "réponse non sourcée" case: the model
        # replied from its weights. Recording that would freeze an ungrounded answer into the
        # demo and make it look deliberate, so it is refused outright.
        if not tools:
            print("        REFUSED: no subordinate fired - the answer is not sourced.")
            continue
        if not text.strip():
            print("        REFUSED: empty answer.")
            continue

        answers[e["q"]] = {
            "persona": e["persona"],
            "expects": e["expects"],
            "text": text,
            "toolsFired": tools,
            "seconds": secs,
            "capturedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        payload["capturedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_answers(payload)
        ok += 1
        print(f"        OK {secs}s, {len(text)} chars, tools={tools}")

    print("\n" + "-" * 74)
    print(f"   {ok}/{len(todo)} recorded, {len(answers)} in the file")
    print(f"   -> {ANSWERS}")
    if ok < len(todo):
        print("   The rest stay live: a missing recording costs latency, never correctness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
