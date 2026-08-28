"""Generate the Voice-of-Customer corpus: what customers SAY, not what the tables already know.

Why this file exists
--------------------
The RAG agent needs a source, not a paraphrase. The pre-existing corpus under
`data/raw/text/customer_knowledge_notes` is 1500 files of exactly 115 characters built from a
single template restating `total_orders` / `total_spend_eur` / `nps_last` -- three columns the
semantic model already answers, faster and exactly. Retrieving over that would make the
document agent a slower copy of the numbers, which is the very trap this project already
fixed once on `churn_risk_score`.

So this corpus carries the one thing the tables do NOT hold: the REASON, in the customer's
own words.

The two rules, and why they are not negotiable
---------------------------------------------
1. BEHAVIOUR FIRST. The complaint motive is drawn from what the customer actually received
   (CAMP_007 send intensity), never from `churn_risk_score` or `risk_band`. A corpus derived
   from the labels would correlate with everything and explain nothing -- the predecessor's
   mistake.

2. NO FIGURE, NO VERIFIABLE STATE. A verbatim may say "je recois beaucoup trop de mails";
   it may never say "j'ai recu quatre mails" or "je me suis desabonne". Measured on the
   deployed Lakehouse (probe, 5 questions):

       population   -- SEG_HIGH_VALUE 3989 = 3989, CAMP_007 targeted 3971 = 3971   IDENTICAL
       per-row draw -- CAMP_007 send rows 15218 vs 15430, unsubscribes 439 vs 247  DIVERGES

   The identity of the people is stable, their counters are not. Anchoring a verbatim on a
   customer_id is therefore safe; putting a number inside it would contradict Fabric on
   stage. Numbers belong to the semantic model, motives belong here, and the supervisor
   combines them. `test_voc.py` enforces the no-digit rule.

Output: one file per verbatim (clean retrieval chunk + citable), plus `_manifest.json`
carrying the motive for the tests only -- the underscore keeps it out of the upload set,
because a manifest naming each motive would let the agent filter instead of read.
"""
from __future__ import annotations

import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
OUT = RAW / "text" / "voice_of_customer"
MANIFEST = OUT / "_manifest.json"

SEED = 42
CAMPAIGN = "CAMP_007"
SEGMENT = "SEG_HIGH_VALUE"
TOTAL_VERBATIMS = 1000
VICTIM_SHARE = 0.62  # rest is the control group, so a motive can be compared against a baseline

EMAIL_PRESSURE = "email_pressure"
MOTIVES = (EMAIL_PRESSURE, "price", "product_quality", "delivery", "service")

# Baseline mix once email_pressure has been decided against. Deliberately flat-ish: the point
# is that pressure DOMINATES among the over-mailed and is marginal elsewhere, which is what
# makes the root-cause analysis a real discrimination instead of a lookup.
OTHER_WEIGHTS = {"price": 0.30, "product_quality": 0.26, "delivery": 0.24, "service": 0.20}

CHANNELS = ("email", "phone", "web", "store")
KINDS = ("ticket", "chat", "email", "call", "survey")

# Channel and kind are not independent: a chat does not happen over the phone, and a call
# does not arrive by email. Drawing them separately produced "Canal: phone / Type: chat",
# which reads as broken data the moment anyone opens a file on stage.
KIND_CHANNELS = {
    "ticket": ("web", "email"),
    "chat": ("web",),
    "email": ("email",),
    "call": ("phone",),
    "survey": ("web", "email", "store"),
}

CAMPAIGN_START = date(2026, 1, 31)
CAMPAIGN_END = date(2026, 2, 20)

# Combinatorial fragments: opening x grievance x closing. A single template is what made the
# previous corpus useless -- 1500 files that all say the same thing retrieve as one document.
OPENINGS = (
    "Bonjour,", "Bonjour, je me permets de vous ecrire.", "Message laisse au service client.",
    "Suite a votre demande d'avis,", "Je vous contacte a nouveau.",
    "Retour transmis lors de l'echange.", "Commentaire laisse apres mon passage.",
    "Je souhaitais vous faire part de mon ressenti.", "Petit retour de ma part.",
    "Bonjour, client chez vous depuis un moment.",
)

GRIEVANCES = {
    EMAIL_PRESSURE: (
        "je recois beaucoup trop de messages de votre part en ce moment",
        "vos sollicitations par mail sont devenues incessantes",
        "ma boite mail est saturee par vos envois",
        "vous m'ecrivez bien trop souvent, c'est etouffant",
        "le rythme de vos emails est franchement excessif",
        "je ne compte plus vos relances, elles arrivent sans arret",
        "je n'ouvre plus vos messages tant il y en a",
        "la frequence de vos communications est devenue penible",
        "j'ai l'impression d'etre harcele par vos campagnes",
        "vos emails promotionnels arrivent en rafale",
        "je subis vos envois plus que je ne les lis",
        "trop de messages tuent le message, chez vous c'est flagrant",
    ),
    "price": (
        "vos tarifs ont nettement augmente sans explication",
        "je trouve vos prix difficiles a justifier aujourd'hui",
        "vos promotions sont incomprehensibles d'une semaine a l'autre",
        "le rapport qualite prix s'est degrade",
        "j'ai trouve le meme article bien moins cher ailleurs",
        "vos remises sont reservees aux nouveaux clients, c'est vexant",
        "le prix affiche ne correspond pas a celui en caisse",
        "vos frais annexes s'accumulent discretement",
    ),
    "product_quality": (
        "la qualite de vos articles n'est plus celle d'avant",
        "le produit recu ne ressemble pas a la description",
        "les finitions laissent vraiment a desirer",
        "l'article s'est abime tres rapidement a l'usage",
        "la matiere est decevante par rapport aux photos",
        "les tailles ne sont pas fiables d'un modele a l'autre",
        "j'ai du renvoyer l'article, il ne correspondait pas",
    ),
    "delivery": (
        "ma commande est arrivee bien apres la date annoncee",
        "le colis etait abime a la reception",
        "le suivi de livraison n'a jamais ete mis a jour",
        "le transporteur est passe sans jamais sonner",
        "la livraison a ete reportee sans que personne ne me previenne",
        "j'ai recu une partie de ma commande seulement",
        "le point relais indique etait ferme",
    ),
    "service": (
        "personne ne repond quand j'appelle le service client",
        "ma demande est restee sans reponse",
        "on m'a fait repeter mon probleme a chaque interlocuteur",
        "le conseiller n'a pas su me repondre et m'a raccroche au nez",
        "j'attends toujours le rappel qu'on m'avait promis",
        "les reponses recues sont manifestement automatiques",
        "impossible de joindre quelqu'un qui prenne le dossier en main",
    ),
}

CLOSINGS = {
    EMAIL_PRESSURE: (
        "Merci de lever le pied sur les envois.",
        "Si cela continue je couperai le contact.",
        "J'aimerais pouvoir choisir ce que je recois.",
        "Je vous demande d'espacer vos communications.",
        "A ce rythme je ne lirai plus rien du tout.",
        "Merci d'en tenir compte.",
        "Cela me donne surtout envie de partir.",
    ),
    "default": (
        "Merci de votre retour.",
        "J'espere que cela sera pris en compte.",
        "Je reste dans l'attente d'une reponse.",
        "Cela m'a vraiment refroidi.",
        "Je comptais sur mieux de votre part.",
        "Merci d'y remedier.",
        "Je vous laisse voir ce qui est possible.",
    ),
}

# The corpus must never assert a number or a verifiable state -- see the module docstring.
FORBIDDEN_WORDS = (
    "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
    "desabonne", "desabonner", "desinscrit", "commandes", "euros",
)


def load_csv(rel: str) -> list[dict]:
    with (RAW / rel).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def campaign_intensity() -> dict[str, int]:
    """How many CAMP_007 emails each customer received. The behavioural driver."""
    sends = load_csv("marketing/marketing_sends.csv")
    return Counter(s["customer_id"] for s in sends if s["campaign_id"] == CAMPAIGN)


def high_value_members() -> set[str]:
    rows = load_csv("crm/crm_customer_segments.csv")
    return {r["customer_id"] for r in rows if r["segment_id"] == SEGMENT}


def pressure_probability(sends: int) -> float:
    """Probability the customer complains about email pressure, given what they received.

    Drawn from behaviour only. A customer outside the campaign still has a small baseline --
    every mailing list annoys someone -- otherwise the signal would be a perfect separator
    and the root-cause analysis would be trivial rather than a discrimination.
    """
    if sends <= 0:
        return 0.05
    return min(0.70, 0.22 + 0.11 * sends)


def pick_motive(rng: random.Random, sends: int) -> str:
    if rng.random() < pressure_probability(sends):
        return EMAIL_PRESSURE
    names = list(OTHER_WEIGHTS)
    return rng.choices(names, weights=[OTHER_WEIGHTS[n] for n in names], k=1)[0]


def pick_date(rng: random.Random, motive: str) -> date:
    """Pressure complaints cluster on the campaign window; the rest spread over the year.

    This is what lets a time-based question separate the cohorts without anyone labelling
    them, and it mirrors the sends -- which are the part of the draw that matched the
    Lakehouse exactly.
    """
    if motive == EMAIL_PRESSURE:
        span = (CAMPAIGN_END - CAMPAIGN_START).days
        return CAMPAIGN_START + timedelta(days=rng.randint(0, span + 21))
    return date(2026, 1, 1) + timedelta(days=rng.randint(0, 330))


def compose(rng: random.Random, motive: str) -> str:
    """Assemble opening + grievance + closing.

    The grievance is capitalised only when the opening ended a sentence: after "Bonjour,"
    it must stay lowercase or the text reads "Bonjour, Je recois...".
    """
    opening = rng.choice(OPENINGS)
    grievance = rng.choice(GRIEVANCES[motive])
    closing = rng.choice(CLOSINGS.get(motive, CLOSINGS["default"]))
    body = grievance if opening.rstrip().endswith(",") else grievance[0].upper() + grievance[1:]
    return f"{opening} {body}. {closing}"


def render(vid: str, meta: dict, text: str) -> str:
    return (
        f"# Verbatim client {vid}\n\n"
        f"- Client : {meta['customer_id']}\n"
        f"- Segment : {meta['segment']}\n"
        f"- Canal : {meta['channel']}\n"
        f"- Type : {meta['kind']}\n"
        f"- Date : {meta['occurred_at']}\n\n"
        f"{text}\n"
    )


def contains_forbidden(text: str) -> list[str]:
    """Return every no-figure violation found, so the caller can fail loudly."""
    found = []
    if re.search(r"\d", text):
        found.append("digit")
    low = text.lower()
    found.extend(w for w in FORBIDDEN_WORDS if re.search(rf"\b{w}\b", low))
    return found


def build(total: int = TOTAL_VERBATIMS, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    intensity = campaign_intensity()
    hv = high_value_members()

    targeted = sorted(c for c, n in intensity.items() if n > 0)
    control = sorted(hv - set(targeted)) or sorted(hv)
    everyone = {c["customer_id"] for c in load_csv("crm/crm_customers.csv")}
    control = sorted(set(control) | (everyone - set(targeted)))

    n_victim = int(total * VICTIM_SHARE)
    authors = ([rng.choice(targeted) for _ in range(n_victim)]
               + [rng.choice(control) for _ in range(total - n_victim)])
    rng.shuffle(authors)

    records = []
    for i, cust in enumerate(authors, start=1):
        sends = intensity.get(cust, 0)
        motive = pick_motive(rng, sends)
        text = compose(rng, motive)
        bad = contains_forbidden(text)
        if bad:
            raise SystemExit(
                f"Verbatim for {cust} breaks the no-figure rule ({bad}): {text!r}. "
                "Fix the fragment lists -- the corpus must never assert a number or a state."
            )
        kind = rng.choice(KINDS)
        meta = {
            "id": f"VOC_{i:05d}",
            "customer_id": cust,
            "segment": SEGMENT if cust in hv else "OTHER",
            "channel": rng.choice(KIND_CHANNELS[kind]),
            "kind": kind,
            "occurred_at": pick_date(rng, motive).isoformat(),
            "motive": motive,
            "campaign_sends": sends,
        }
        records.append({**meta, "text": text})
    return records


def write(records: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("VOC_*.md"):
        stale.unlink()
    for r in records:
        meta = {k: v for k, v in r.items() if k not in ("text",)}
        (OUT / f"{r['id']}.md").write_text(render(r["id"], meta, r["text"]), encoding="utf-8")
    MANIFEST.write_text(
        json.dumps([{k: v for k, v in r.items()} for r in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summarise(records: list[dict]) -> None:
    by_cohort: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        cohort = "cible CAMP_007" if r["campaign_sends"] > 0 else "temoin"
        by_cohort[cohort][r["motive"]] += 1
    print(f"\n{len(records)} verbatims -> {OUT}")
    for cohort, counts in by_cohort.items():
        tot = sum(counts.values())
        share = counts[EMAIL_PRESSURE] / tot * 100 if tot else 0
        print(f"\n  {cohort} ({tot}) -- pression email {share:.1f}%")
        for motive, n in counts.most_common():
            print(f"      {motive:16} {n:5}  {n / tot * 100:5.1f}%")
    uniq = len({r["text"] for r in records})
    print(f"\n  textes distincts : {uniq}/{len(records)}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not (RAW / "marketing" / "marketing_sends.csv").exists():
        raise SystemExit(f"{RAW} has no marketing_sends.csv -- run generate_data.py first.")
    records = build()
    write(records)
    summarise(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
