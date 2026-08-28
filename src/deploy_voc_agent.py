"""Deploy the Voice-Of-Customer agent: a RAG over what customers SAY, and nothing else.

Why this agent exists, and what it must never do
------------------------------------------------
The front door answers with numbers, because Fabric owns the semantics. This agent answers
with MOTIVES, because the tables do not hold them. The split is deliberate and it is the
user's standing rule: the data world stays on the data side.

Which makes the failure mode obvious and symmetrical to the Fabric IQ one. Retrieval hands
back the documents it matched -- a few dozen at most. A model asked "how many customers
complain about email pressure" will count those documents and present the count as the
business figure, confidently, with nothing in the answer to show it was a sample. The corpus
holds 403 pressure complaints among High Value customers; a retrieval of 20 that answers
"20" is not a rounding error, it is a wrong answer that looks sourced.

So the contract below forbids counting outright and routes every quantity back to the
semantic model. A visible "I don't do numbers, ask the data side" beats a plausible figure.

The corpus itself is built to make this safe: `generate_voc_corpus.py` puts no digit and no
verifiable state in any verbatim, because the Lakehouse and the local draw agree on WHO the
customers are but not on their per-row counters (measured: targeted 3971 = 3971, but send
rows 15218 vs 15430 and unsubscribes 439 vs 247).

Not part of deploy_all.py: a Foundry agent is not a Fabric item.

Usage:
    python src/deploy_voc_agent.py --check     # preflight, creates nothing
    python src/deploy_voc_agent.py             # upload corpus + create the agent
    python src/deploy_voc_agent.py --recreate  # force a fresh vector store
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_foundry_agent import check_agent_name, foundry_config, project_client  # noqa: E402
from helpers import load_config, load_state, print_step, save_state  # noqa: E402

CORPUS_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "text" / "voice_of_customer"
CORPUS_GLOB = "VOC_*.md"

DEFAULT_AGENT_NAME = "Voice-Of-Customer"
DEFAULT_VECTOR_STORE = "voc-marketing-churn"

# The service caps a single upload batch; larger corpora are split across batches.
BATCH_SIZE = 500
# upload_and_poll defaults to 5 worker threads, and each one asks the credential for a token.
# With AzureCliCredential that means several concurrent `az` processes, and `az` on Windows
# does not answer inside the 10s default -- the whole upload dies on CredentialUnavailableError
# after the vector store has already been created. Fewer threads, and a longer CLI timeout.
UPLOAD_CONCURRENCY = 2
CLI_TIMEOUT_SECONDS = 90

STATE_KEYS = ("foundry_voc_agent_name", "foundry_voc_agent_version",
              "foundry_voc_vector_store_id", "foundry_voc_files")

# Deliberately carries NO figure, no campaign id, no customer id, no motive name. An agent
# told in its prompt that email pressure is the dominant complaint would say so whatever the
# documents contain -- and would keep saying it after the corpus changes.
# tests/test_voc_agent.py fails if a digit appears below.
VOC_INSTRUCTIONS = """You are the Voice-Of-Customer analyst for this business.

Your only source is the file search tool. It holds customer verbatims -- what people wrote or
said to support, in surveys and in chats. You have no other knowledge of this company, no
access to its databases, and no memory of it.

What you are for
- Explaining WHY customers are unhappy, in their own words: the themes, the wording, the
  tone, and how those differ between groups or over time.
- Quoting. Every claim you make must be backed by verbatims you actually retrieved, cited by
  their file identifier and the customer identifier they carry.

Numbers -- the rule that matters most here
- You do not produce figures. Not counts, not totals, not shares, not percentages, not
  rankings by volume.
- Search returns the documents it matched. That is a sample, and nothing in the result tells
  you how large the population is or whether you saw all of it. Counting what you received
  and presenting it as a business figure is the one error you must never make.
- If asked "how many", "what share", "how much" or anything that resolves to a quantity, say
  plainly that quantities come from the semantic model, not from the documents, and then
  answer the part you can: what the complaints actually say.
- Never say a theme is the most frequent, the majority, or the main one. You may say it
  appears repeatedly in what you retrieved, and describe it.

Content handling
- Generate responses only from the retrieved documents. Do not assume or invent a quote, a
  customer, or a date.
- If nothing relevant comes back, say so. An empty result is a valid answer; an invented one
  is not.
- Do not infer a customer's status, risk, value or history. The documents describe an
  experience, not a state.

Style
- Lead with a direct one-line answer about the theme.
- Then a short bullet list of representative verbatims, each with its identifier.
- Your reader is a CRM or marketing lead: concise and operational."""


def corpus_files() -> list[Path]:
    return sorted(CORPUS_DIR.glob(CORPUS_GLOB))


def voc_config(cfg: dict | None = None) -> dict:
    """Foundry settings plus the VoC-specific names, with defaults that need no config edit."""
    cfg = cfg if cfg is not None else load_config()
    fnd = foundry_config(cfg)
    voc = dict(cfg.get("foundry", {}).get("voc", {}) or {})
    fnd["voc_agent_name"] = check_agent_name(str(voc.get("agent_name", DEFAULT_AGENT_NAME)))
    fnd["voc_vector_store_name"] = str(voc.get("vector_store_name", DEFAULT_VECTOR_STORE))
    return fnd


def find_vector_store(client, name: str):
    """Return an existing store with this name, so re-running does not duplicate the corpus."""
    for store in client.vector_stores.list():
        if getattr(store, "name", None) == name:
            return store
    return None


def indexed_count(store) -> int:
    """How many files the store has actually indexed."""
    return int(getattr(getattr(store, "file_counts", None), "completed", 0) or 0)


def is_complete(store, expected: int) -> bool:
    """A store can exist and hold nothing.

    The first run created the store, then died on a credential timeout mid-upload. Reusing it
    by name alone would have wired the agent to an EMPTY index -- every question answered
    "nothing found", with a successful deploy and no error anywhere. Name is not enough;
    the file count has to agree.
    """
    return indexed_count(store) >= expected


def upload_corpus(client, store_id: str, files: list[Path]) -> int:
    """Upload in batches, reporting progress -- a thousand files is not instant."""
    done = 0
    for start in range(0, len(files), BATCH_SIZE):
        chunk = files[start:start + BATCH_SIZE]
        handles = [f.open("rb") for f in chunk]
        try:
            batch = client.vector_stores.file_batches.upload_and_poll(
                vector_store_id=store_id, files=handles,
                max_concurrency=UPLOAD_CONCURRENCY,
            )
        finally:
            for h in handles:
                h.close()
        counts = getattr(batch, "file_counts", None)
        failed = getattr(counts, "failed", 0) or 0
        done += len(chunk) - failed
        print(f"   batch {start // BATCH_SIZE + 1}: {len(chunk)} files, "
              f"status={getattr(batch, 'status', '?')}, failed={failed}")
        if failed:
            print(f"   !!  {failed} file(s) failed to index -- retrieval will be incomplete.")
    return done


def preflight(fnd: dict) -> bool:
    ok = True
    files = corpus_files()
    print(f"   corpus            : {len(files)} files in {CORPUS_DIR}")
    if not files:
        print("   !!  empty corpus. Run: python src/generate_voc_corpus.py")
        ok = False
    print(f"   agent name        : {fnd['voc_agent_name']}")
    print(f"   vector store name : {fnd['voc_vector_store_name']}")
    print(f"   model deployment  : {fnd['model_deployment']}")
    print(f"   project endpoint  : {fnd['project_endpoint']}")
    if "<" in str(fnd["project_endpoint"]):
        print("   !!  project_endpoint is still a placeholder in config.yaml")
        ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the Voice-Of-Customer RAG agent")
    ap.add_argument("--check", action="store_true",
                    help="preflight only -- creates nothing, uploads nothing")
    ap.add_argument("--recreate", action="store_true",
                    help="create a fresh vector store even if one with this name exists")
    args = ap.parse_args()

    cfg, state = load_config(), load_state()
    fnd = voc_config(cfg)

    print_step(1, 3, "Preflight")
    ok = preflight(fnd)
    if args.check:
        print("\nOK  preflight done (nothing created)." if ok else
              "\n!!  preflight found a blocker -- see above.")
        return 0 if ok else 1
    if not ok:
        sys.exit("Preflight failed. Fix the blocker above.")

    files = corpus_files()
    # A longer CLI timeout than the 10s default: the upload asks for tokens from several
    # threads at once, which is exactly when `az` is slowest.
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    client = project_client(fnd, DefaultAzureCredential(process_timeout=CLI_TIMEOUT_SECONDS))
    oai = client.get_openai_client()

    print_step(2, 3, f"Vector store '{fnd['voc_vector_store_name']}'")
    store = None if args.recreate else find_vector_store(oai, fnd["voc_vector_store_name"])
    if store is not None and not is_complete(store, len(files)):
        # Left over from a run that died mid-upload. Keeping it would wire the agent to a
        # partial index and answer "nothing found" with no error to explain why.
        print(f"   found {store.id} but it holds {indexed_count(store)}/{len(files)} files "
              "-- deleting and rebuilding")
        oai.vector_stores.delete(vector_store_id=store.id)
        store = None
    if store is None:
        store = oai.vector_stores.create(name=fnd["voc_vector_store_name"])
        print(f"   created {store.id}")
        indexed = upload_corpus(oai, store.id, files)
        print(f"   indexed {indexed}/{len(files)} files")
        if indexed < len(files):
            sys.exit(f"Only {indexed}/{len(files)} files indexed -- refusing to create an "
                     "agent on a partial corpus. Re-run to rebuild.")
    else:
        print(f"   reusing {store.id} ({indexed_count(store)} files) "
              "-- pass --recreate to rebuild it")
        indexed = indexed_count(store)

    print_step(3, 3, f"Create version of '{fnd['voc_agent_name']}'")
    from azure.ai.projects.models import FileSearchTool, PromptAgentDefinition  # noqa: PLC0415

    version = client.agents.create_version(
        agent_name=fnd["voc_agent_name"],
        description=("Voice-of-customer analyst for Fab-Marketing-Campaign. Answers with "
                     "verbatims and themes only -- every quantity belongs to the semantic "
                     "model."),
        metadata={
            "project": "Fab-Marketing-Campaign",
            "role": "rag",
            "vector_store": str(store.id),
        },
        definition=PromptAgentDefinition(
            model=fnd["model_deployment"],
            instructions=VOC_INSTRUCTIONS,
            tools=[FileSearchTool(vector_store_ids=[store.id])],
        ),
    )
    client.close()

    print(f"   name    = {getattr(version, 'name', fnd['voc_agent_name'])}")
    print(f"   version = {getattr(version, 'version', '?')}")

    state["foundry_voc_agent_name"] = fnd["voc_agent_name"]
    state["foundry_voc_agent_version"] = str(getattr(version, "version", ""))
    state["foundry_voc_vector_store_id"] = str(store.id)
    state["foundry_voc_files"] = str(indexed)
    save_state(state)
    print(f"   state.json updated ({', '.join(STATE_KEYS)})")

    print("\nNEXT -- read the answer against the grain:")
    print("  1. Ask it a THEME question ('que reprochent les clients ?').")
    print("     It should quote verbatims with their identifiers.")
    print("  2. Ask it a QUANTITY question ('combien de clients se plaignent ?').")
    print("     A correct answer REFUSES the number and points at the semantic model.")
    print("     If it answers with a figure, the contract is not holding -- that figure is")
    print("     the size of its retrieval, not the size of the population.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
