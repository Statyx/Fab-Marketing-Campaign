#!/usr/bin/env python3
"""
Generate the Customer 360 dataset (CRM + Marketing + Commerce) for Fab-Marketing-Campaign.

DESIGN RULE — the whole point of this generator:
    Behaviour comes FIRST, labels come LAST.

    We simulate what each customer actually did (orders, sends, opens, clicks, unsubscribes,
    support interactions), and only THEN derive churn_risk_score / CLV / lifecycle from that
    behaviour. Nothing about churn is random.

    This is a deliberate break from the earlier prototype, where churn_risk_score was drawn from
    a distribution and correlated with NOTHING (|r| < 0.02 against every behavioural signal),
    which made every churn question unanswerable and any ML story impossible.

THE STORYLINE (config: storyline)
    Campaign CAMP_007 "Black Friday Blast" over-mails the SEG_HIGH_VALUE segment
    -> unsubscribe spike -> engagement collapse -> orders stop -> churn.
    The signal is embedded in the DATA, so RCA genuinely finds it.

Outputs 15 CSVs under data/raw/{crm,marketing,commerce}/ plus a text corpus.

Run:  python generate_data.py
"""
import os, sys, winreg


def _restore_path():
    parts = []
    for root, sub in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")]:
        try:
            k = winreg.OpenKey(root, sub); v, _ = winreg.QueryValueEx(k, "Path")
            parts.append(os.path.expandvars(v)); winreg.CloseKey(k)
        except Exception:
            pass
    if parts:
        os.environ["PATH"] = ";".join(parts) + ";" + os.environ.get("PATH", "")


_restore_path()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

SCRIPT_DIR = Path(__file__).parent
RAW = SCRIPT_DIR.parent / "data" / "raw"
SEED = 42

# Basket shape — defined once so product pricing can be derived from the target AOV.
LINES_PER_ORDER = ([1, 2, 3, 4], [0.45, 0.30, 0.17, 0.08])
QTY_PER_LINE = ([1, 2, 3], [0.75, 0.20, 0.05])


def _expected_units_per_order():
    lines = sum(v * w for v, w in zip(*LINES_PER_ORDER))
    qty = sum(v * w for v, w in zip(*QTY_PER_LINE))
    return lines * qty


def load_config():
    with open(SCRIPT_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _rng(cfg):
    random.seed(SEED)
    np.random.seed(SEED)
    return random.Random(SEED)


def _norm(x, lo, hi):
    """Clamp x into 0..1 given a lo..hi range."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


# ══════════════════════════════════════════════════════════════════
# 1. CRM — accounts, customers, segments
# ══════════════════════════════════════════════════════════════════

FIRST = ["Camille", "Lucas", "Emma", "Hugo", "Chloe", "Louis", "Manon", "Nathan", "Sarah", "Leo",
         "Ines", "Gabriel", "Julie", "Adam", "Lea", "Raphael", "Anna", "Jules", "Alice", "Paul"]
LAST = ["Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy",
        "Moreau", "Simon", "Laurent", "Lefebvre", "Michel", "Garcia", "David", "Bertrand", "Roux"]
CITIES = ["Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux", "Lille", "Nantes", "Nice",
          "Strasbourg", "Rennes", "Montpellier", "Grenoble"]
INDUSTRIES = ["Retail", "Manufacturing", "Services", "Public", "Healthcare", "Tech"]


def build_accounts(cfg, rng):
    rows = []
    for i in range(cfg["volumes"]["accounts"]):
        rows.append({
            "account_id": f"ACC_{i+1:06d}",
            "account_name": f"{rng.choice(LAST)} {rng.choice(['SAS', 'SARL', 'Group', 'Partners'])}",
            "industry": rng.choice(INDUSTRIES),
            "city": rng.choice(CITIES),
            "employees": rng.choice([12, 45, 120, 350, 900, 2500]),
            "created_at": (datetime(2020, 1, 1) + timedelta(days=rng.randint(0, 2000))).strftime("%Y-%m-%d"),
        })
    return pd.DataFrame(rows)


def build_customers(cfg, rng, accounts):
    ref = datetime.fromisoformat(cfg["business"]["reference_date"])
    window = cfg["business"]["window_days"]
    mix = cfg["business"]["lifecycle_mix"]
    stages = [m["stage"] for m in mix]
    weights = [m["weight"] for m in mix]
    acc_ids = accounts["account_id"].tolist()

    rows = []
    for i in range(cfg["volumes"]["customers"]):
        cid = f"CUST_{i+1:06d}"
        stage = rng.choices(stages, weights=weights)[0]
        is_b2b = rng.random() < 0.35
        first_seen = ref - timedelta(days=rng.randint(30, window + 400))
        # Consent: unsubscribed customers are handled later (behaviour decides), start opted-in
        rows.append({
            "customer_id": cid,
            "account_id": rng.choice(acc_ids) if is_b2b else "",
            "first_name": rng.choice(FIRST),
            "last_name": rng.choice(LAST),
            "email": f"{cid.lower()}@example.com",
            "city": rng.choice(CITIES),
            "customer_type": "B2B" if is_b2b else "B2C",
            "lifecycle_stage": stage,          # provisional — recomputed from behaviour later
            "consent_email": True,             # provisional — unsubscribes applied later
            "first_seen_at": first_seen.strftime("%Y-%m-%d"),
        })
    return pd.DataFrame(rows)


def build_segments(cfg):
    rows = []
    for s in cfg["segments"]:
        rows.append({
            "segment_id": s["id"],
            "segment_name": s["name"],
            "definition": s["rule"],
            "is_premium": s["premium"],
        })
    return pd.DataFrame(rows)


def assign_segments(cfg, rng, customers):
    """Static segment membership (behavioural segments are re-derived at the end)."""
    high_value = cfg["storyline"]["victim_segment_id"]
    rows = []
    for c in customers.itertuples():
        # every customer belongs to 1-3 segments; SEG_HIGH_VALUE membership drives the storyline
        n = rng.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
        pool = [s["id"] for s in cfg["segments"]]
        chosen = rng.sample(pool, n)
        # make the victim segment meaningfully sized (~22% of the base)
        if rng.random() < 0.22 and high_value not in chosen:
            chosen.append(high_value)
        for sid in chosen:
            rows.append({"customer_id": c.customer_id, "segment_id": sid,
                         "assigned_at": c.first_seen_at})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════
# 2. Marketing — campaigns, assets, audiences
# ══════════════════════════════════════════════════════════════════

CAMPAIGN_NAMES = [
    "Spring Collection", "Summer Sale", "Loyalty Boost", "New Arrivals", "Flash Deals",
    "Back to School", "Black Friday Blast", "Cyber Monday", "Christmas Gifts", "Winter Clearance",
    "Welcome Series", "Reactivation Push", "VIP Preview", "Referral Program", "Birthday Offer",
    "Cart Reminder", "Product Launch", "Seasonal Digest", "Members Only", "Year End Thanks",
]


def build_campaigns(cfg, rng):
    ref = datetime.fromisoformat(cfg["business"]["reference_date"])
    window = cfg["business"]["window_days"]
    objs = cfg["campaign_objectives"]
    culprit = cfg["storyline"]["culprit_campaign_id"]

    rows = []
    for i, name in enumerate(CAMPAIGN_NAMES[: cfg["volumes"]["campaigns"]]):
        cid = f"CAMP_{i+1:03d}"
        # spread campaigns across the window; the culprit sits ~5 months before the reference date
        if cid == culprit:
            start = ref - timedelta(days=150)
            objective = "retention"
            name = cfg["storyline"]["culprit_campaign_name"]
        else:
            start = ref - timedelta(days=rng.randint(20, window))
            objective = rng.choices([o["name"] for o in objs], weights=[o["weight"] for o in objs])[0]
        rows.append({
            "campaign_id": cid,
            "campaign_name": name,
            "objective": objective,
            "channel": "email",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": (start + timedelta(days=rng.randint(5, 21))).strftime("%Y-%m-%d"),
            "budget_eur": rng.randint(4000, 45000),
        })
    return pd.DataFrame(rows)


def build_assets(cfg, rng, campaigns):
    rows = []
    for c in campaigns.itertuples():
        for v in range(cfg["volumes"]["assets_per_campaign"]):
            variant = ["A", "B", "C"][v]
            rows.append({
                "asset_id": f"ASSET_{c.campaign_id}_{variant}",
                "campaign_id": c.campaign_id,
                "variant": variant,
                "subject_line": f"{c.campaign_name} - variante {variant}",
                "cta": rng.choice(["Shop now", "Discover", "Claim offer", "See more"]),
            })
    return pd.DataFrame(rows)


def build_audiences(cfg, rng, campaigns):
    rows = []
    culprit = cfg["storyline"]["culprit_campaign_id"]
    victim = cfg["storyline"]["victim_segment_id"]
    seg_ids = [s["id"] for s in cfg["segments"]]
    for c in campaigns.itertuples():
        if c.campaign_id == culprit:
            targets = [victim]                     # the culprit targets ONLY the high-value segment
        else:
            targets = rng.sample(seg_ids, rng.randint(1, 3))
        for sid in targets:
            rows.append({"campaign_id": c.campaign_id, "segment_id": sid})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════
# 3. Behaviour simulation — sends, events, orders (the real work)
# ══════════════════════════════════════════════════════════════════

def simulate_behaviour(cfg, rng, customers, campaigns, audiences, cust_segments, products):
    """Simulate every customer's email + purchase behaviour over the window.

    Returns sends, events, orders, order_lines, returns and a per-customer behaviour record
    that the churn model consumes.
    """
    b = cfg["business"]
    sl = cfg["storyline"]
    ref = datetime.fromisoformat(b["reference_date"])
    window = b["window_days"]
    culprit = sl["culprit_campaign_id"]
    victim = sl["victim_segment_id"]

    seg_of = cust_segments.groupby("customer_id")["segment_id"].apply(set).to_dict()
    camp_segments = audiences.groupby("campaign_id")["segment_id"].apply(set).to_dict()
    camp_start = {c.campaign_id: datetime.fromisoformat(c.start_date) for c in campaigns.itertuples()}
    assets_of = {}
    for cid in campaigns["campaign_id"]:
        assets_of[cid] = [f"ASSET_{cid}_{v}" for v in ["A", "B", "C"][: cfg["volumes"]["assets_per_campaign"]]]

    prod = products.to_dict("records")

    sends, events, orders, order_lines, returns = [], [], [], [], []
    behaviour = {}
    send_n = event_n = order_n = line_n = ret_n = 0

    for c in customers.itertuples():
        cid = c.customer_id
        segs = seg_of.get(cid, set())
        in_victim_segment = (victim in segs)
        # Only a share of the targeted segment actually burns out — the rest absorb the pressure.
        fatigued = in_victim_segment and (rng.random() < sl["fatigue_share"])
        culprit_date = camp_start[culprit]

        # ── per-customer engagement baseline (stable personality) ──
        open_base = max(0.02, min(0.85, np.random.normal(b["open_rate_baseline"], 0.10)))
        click_base = max(0.01, min(0.60, np.random.normal(b["click_rate_of_open"], 0.04)))
        unsubscribed = False
        unsub_date = None

        # ── email sends ──
        cust_sends = []
        for camp in campaigns.itertuples():
            camp_id = camp.campaign_id
            if not (camp_segments.get(camp_id, set()) & segs):
                continue
            n_sends = 1
            if camp_id == culprit and in_victim_segment:
                n_sends = int(sl["fatigue_send_multiplier"])      # THE ROOT CAUSE: over-mailing
            for k in range(n_sends):
                if unsubscribed:
                    break
                ts = camp_start[camp_id] + timedelta(days=k * 2, hours=rng.randint(6, 20))
                if ts > ref:
                    continue
                send_n += 1
                sid = f"SEND_{send_n:08d}"
                cust_sends.append((sid, camp_id, ts))
                sends.append({
                    "send_id": sid, "campaign_id": camp_id, "customer_id": cid,
                    "asset_id": rng.choice(assets_of[camp_id]),
                    "sent_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                })

                # ── events for this send ──
                is_culprit_send = (camp_id == culprit and in_victim_segment)
                # Engagement collapses for the fatigued cohort from the culprit campaign onwards.
                decay = sl["fatigue_engagement_decay"] if (fatigued and ts >= culprit_date) else 1.0
                if rng.random() < b["bounce_rate"]:
                    event_n += 1
                    events.append({"event_id": f"EVT_{event_n:08d}", "send_id": sid,
                                   "customer_id": cid, "campaign_id": camp_id,
                                   "event_type": "bounce",
                                   "event_at": (ts + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")})
                    continue
                if rng.random() < open_base * decay:
                    event_n += 1
                    o_at = ts + timedelta(hours=rng.randint(1, 40))
                    events.append({"event_id": f"EVT_{event_n:08d}", "send_id": sid,
                                   "customer_id": cid, "campaign_id": camp_id,
                                   "event_type": "open",
                                   "event_at": o_at.strftime("%Y-%m-%d %H:%M:%S")})
                    if rng.random() < click_base * decay:
                        event_n += 1
                        events.append({"event_id": f"EVT_{event_n:08d}", "send_id": sid,
                                       "customer_id": cid, "campaign_id": camp_id,
                                       "event_type": "click",
                                       "event_at": (o_at + timedelta(minutes=rng.randint(1, 90))).strftime("%Y-%m-%d %H:%M:%S")})
                # unsubscribe — massively amplified for the fatigued cohort on the culprit sends
                u_rate = b["unsub_rate_baseline"]
                if is_culprit_send and fatigued:
                    u_rate *= sl["fatigue_unsub_multiplier"]
                if not unsubscribed and rng.random() < u_rate:
                    unsubscribed = True
                    unsub_date = ts + timedelta(hours=1)
                    event_n += 1
                    events.append({"event_id": f"EVT_{event_n:08d}", "send_id": sid,
                                   "customer_id": cid, "campaign_id": camp_id,
                                   "event_type": "unsubscribe",
                                   "event_at": unsub_date.strftime("%Y-%m-%d %H:%M:%S")})

        # ── purchases ──
        stage = c.lifecycle_stage
        base_rate = {"lead": 0.15, "prospect": 0.6, "active": 1.0, "at_risk": 0.7, "churned": 0.4}[stage]
        years = window / 365.0
        n_orders = max(0, int(np.random.poisson(b["orders_per_active_customer_year"] * years * base_rate)))

        first_seen = datetime.fromisoformat(c.first_seen_at)
        earliest = max(first_seen, ref - timedelta(days=window))
        order_dates = []
        for _ in range(n_orders):
            span = max(1, (ref - earliest).days)
            order_dates.append(earliest + timedelta(days=rng.randint(0, span)))
        order_dates.sort()

        # THE CONSEQUENCE: the fatigued cohort stops ordering after the culprit campaign.
        # Those who ALSO unsubscribed are the ones who genuinely walked away — they never
        # come back, so their recency saturates and they land in the Critical band.
        if fatigued:
            if unsubscribed:
                order_dates = [d for d in order_dates if d < culprit_date]
            else:
                gap_start = culprit_date
                gap_end = culprit_date + timedelta(days=sl["fatigue_order_gap_days"])
                order_dates = [d for d in order_dates if not (gap_start <= d <= gap_end)]

        for d in order_dates:
            order_n += 1
            oid = f"ORD_{order_n:07d}"
            n_lines = rng.choices(LINES_PER_ORDER[0], weights=LINES_PER_ORDER[1])[0]
            total = 0.0
            for _ in range(n_lines):
                p = rng.choice(prod)
                qty = rng.choices(QTY_PER_LINE[0], weights=QTY_PER_LINE[1])[0]
                line_total = round(p["unit_price_eur"] * qty, 2)
                total += line_total
                line_n += 1
                order_lines.append({
                    "order_line_id": f"LINE_{line_n:08d}", "order_id": oid,
                    "product_id": p["product_id"], "quantity": qty,
                    "unit_price_eur": p["unit_price_eur"], "line_total_eur": line_total,
                })
            # last-touch attribution: a click within the window before the order
            attributed = ""
            for sid, camp_id, ts in reversed(cust_sends):
                if 0 <= (d - ts).days <= b["attribution_window_days"]:
                    attributed = camp_id
                    break
            orders.append({
                "order_id": oid, "customer_id": cid,
                "order_at": d.strftime("%Y-%m-%d %H:%M:%S"),
                "total_amount_eur": round(total, 2),
                "channel": rng.choices(["web", "app", "store"], weights=[0.6, 0.25, 0.15])[0],
                "attributed_campaign_id": attributed,
            })
            if rng.random() < b["return_rate"]:
                ret_n += 1
                returns.append({
                    "return_id": f"RET_{ret_n:06d}", "order_id": oid, "customer_id": cid,
                    "returned_at": (d + timedelta(days=rng.randint(3, 30))).strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": rng.choice(["damaged", "wrong_size", "not_as_described", "changed_mind"]),
                    "refund_amount_eur": round(total * rng.uniform(0.3, 1.0), 2),
                })

        # ── record behaviour for the churn model ──
        behaviour[cid] = {
            "customer_id": cid,
            "in_victim_segment": in_victim_segment,
            "fatigued": fatigued,
            "unsubscribed": unsubscribed,
            "unsub_at": unsub_date.strftime("%Y-%m-%d %H:%M:%S") if unsub_date else "",
            "n_sends": len(cust_sends),
            "order_dates": order_dates,
            "open_base": open_base,
        }

        if unsubscribed:
            customers.loc[customers["customer_id"] == cid, "consent_email"] = False

    return (pd.DataFrame(sends), pd.DataFrame(events), pd.DataFrame(orders),
            pd.DataFrame(order_lines), pd.DataFrame(returns), behaviour)


def build_products(cfg, rng):
    """Prices are DERIVED from the configured target AOV and the basket shape,
    so `business.avg_order_value_eur` actually controls the outcome."""
    rows = []
    cats = cfg["product_categories"]
    target_aov = cfg["business"]["avg_order_value_eur"]
    mean_price = target_aov / _expected_units_per_order()
    # lognormal spread keeps a realistic long tail without blowing up the mean
    sigma = 0.55
    mu = np.log(mean_price) - (sigma ** 2) / 2
    for i in range(cfg["volumes"]["products"]):
        cat = rng.choice(cats)
        price = float(np.random.lognormal(mu, sigma))
        price = round(max(4.0, min(mean_price * 8, price)), 2)
        rows.append({
            "product_id": f"PROD_{i+1:05d}",
            "product_name": f"{cat['name']} Item {i+1}",
            "category": cat["name"],
            "unit_price_eur": price,
            "margin_pct": cat["margin"],
        })
    return pd.DataFrame(rows)


def build_interactions(cfg, rng, customers, behaviour):
    """Support interactions. Fatigued/unsubscribed customers generate more negative ones."""
    ref = datetime.fromisoformat(cfg["business"]["reference_date"])
    window = cfg["business"]["window_days"]
    types = ["call", "email", "chat", "ticket", "meeting"]
    rows = []
    n = cfg["volumes"]["interactions"]
    cids = customers["customer_id"].tolist()
    for i in range(n):
        cid = rng.choice(cids)
        bh = behaviour.get(cid, {})
        negative_bias = 0.45 if bh.get("unsubscribed") else 0.18
        sentiment = "negative" if rng.random() < negative_bias else rng.choice(["neutral", "positive"])
        resolved = False if (sentiment == "negative" and rng.random() < 0.45) else True
        rows.append({
            "interaction_id": f"INT_{i+1:08d}",
            "customer_id": cid,
            "interaction_type": rng.choice(types),
            "channel": rng.choice(["phone", "email", "web", "store"]),
            "sentiment": sentiment,
            "is_resolved": resolved,
            "occurred_at": (ref - timedelta(days=rng.randint(0, window))).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════
# 4. THE CHURN MODEL — derived from behaviour, never random
# ══════════════════════════════════════════════════════════════════

def build_customer_profile(cfg, rng, customers, orders, events, sends, interactions, behaviour):
    """Aggregate real behaviour, then compute churn_risk_score from it.

    Every column here is COMPUTED. Nothing is drawn from a distribution.
    """
    b = cfg["business"]
    cm = cfg["churn_model"]
    ref = datetime.fromisoformat(b["reference_date"])
    w = cm["weights"]

    # ── real aggregates ──
    o = orders.copy()
    o["order_at"] = pd.to_datetime(o["order_at"])
    agg = o.groupby("customer_id").agg(
        total_orders=("order_id", "count"),
        total_spend_eur=("total_amount_eur", "sum"),
        avg_order_value_eur=("total_amount_eur", "mean"),
        last_order_at=("order_at", "max"),
        first_order_at=("order_at", "min"),
    )
    recent = o[o["order_at"] >= ref - timedelta(days=90)].groupby("customer_id").size().rename("orders_90d")
    prev = o[(o["order_at"] >= ref - timedelta(days=180)) &
             (o["order_at"] < ref - timedelta(days=90))].groupby("customer_id").size().rename("orders_prev_90d")

    ev = events.copy()
    opens = ev[ev["event_type"] == "open"].groupby("customer_id").size().rename("opens")
    clicks = ev[ev["event_type"] == "click"].groupby("customer_id").size().rename("clicks")
    snd = sends.groupby("customer_id").size().rename("sends")

    it = interactions.copy()
    neg = it[it["sentiment"] == "negative"].groupby("customer_id").size().rename("negative_interactions")
    unres = it[(it["sentiment"] == "negative") & (~it["is_resolved"])].groupby("customer_id").size().rename("unresolved_interactions")
    tot_it = it.groupby("customer_id").size().rename("total_interactions")

    df = customers[["customer_id"]].copy()
    for s in [agg, recent, prev, opens, clicks, snd, neg, unres, tot_it]:
        df = df.merge(s, on="customer_id", how="left")
    df = df.fillna({"total_orders": 0, "total_spend_eur": 0.0, "avg_order_value_eur": 0.0,
                    "orders_90d": 0, "orders_prev_90d": 0, "opens": 0, "clicks": 0, "sends": 0,
                    "negative_interactions": 0, "unresolved_interactions": 0, "total_interactions": 0})

    # ── NPS: correlated with real experience (returns, unresolved tickets, engagement) ──
    nps_vals = []
    for r in df.itertuples():
        score = 8.0
        score -= 2.2 * _norm(r.unresolved_interactions, 0, 4)
        score -= 1.5 * _norm(r.negative_interactions, 0, 8)
        score += 1.2 * _norm(r.total_orders, 0, 12)
        score += np.random.normal(0, 0.9)                 # residual noise, not the driver
        nps_vals.append(int(max(0, min(10, round(score)))))
    df["nps_last"] = nps_vals

    # ── behavioural signals, each 0..1 (1 = worst) ──
    days_since = []
    for r in df.itertuples():
        bh = behaviour.get(r.customer_id, {})
        dates = bh.get("order_dates") or []
        days_since.append((ref - max(dates)).days if dates else 999)
    df["days_since_last_order"] = days_since

    df["engagement_rate"] = np.where(df["sends"] > 0, df["opens"] / df["sends"], 0.0)
    df["click_rate"] = np.where(df["opens"] > 0, df["clicks"] / df["opens"], 0.0)
    df["unsubscribed"] = [behaviour.get(c, {}).get("unsubscribed", False) for c in df["customer_id"]]

    s_recency = df["days_since_last_order"].apply(lambda d: _norm(d, 30, 300))

    def _freq_signal(r):
        """Drop in ordering pace, 0..1 (1 = worst).

        A customer with order history but NOTHING in either 90-day window has stopped
        completely — that is the maximum drop, not a neutral 0.5. Getting this wrong caps
        the score and makes the top risk band unreachable.
        """
        if r.orders_90d + r.orders_prev_90d > 0:
            return _norm(-(r.orders_90d - r.orders_prev_90d), 0, 3)
        return 1.0 if r.total_orders > 0 else 0.5

    s_freq = [_freq_signal(r) for r in df.itertuples()]
    s_engage = [
        1.0 - _norm(r.engagement_rate, 0.0, float(b["open_rate_baseline"])) for r in df.itertuples()
    ]
    s_nps = df["nps_last"].apply(lambda n: _norm(10 - n, 0, 10))
    s_unsub = df["unsubscribed"].astype(float)
    s_support = [
        _norm(r.unresolved_interactions, 0, 3) for r in df.itertuples()
    ]

    raw = (w["recency"] * s_recency
           + w["frequency_drop"] * pd.Series(s_freq)
           + w["engagement_decay"] * pd.Series(s_engage)
           + w["nps"] * s_nps
           + w["unsubscribed"] * s_unsub
           + w["support_friction"] * pd.Series(s_support))
    df["churn_risk_score"] = (raw * 100).round().clip(0, 100).astype(int)

    # Churn only means something for someone who actually bought. A contact who never
    # ordered has a CONVERSION problem, not a churn problem — mixing the two is how a
    # "churn cohort" ends up full of prospects and the remediation budget gets wasted.
    df["is_customer"] = df["total_orders"] > 0
    df.loc[~df["is_customer"], "churn_risk_score"] = 0

    # ── risk band label ──
    def band(row):
        if not row.is_customer:
            return "Prospect"
        for bd in cm["risk_bands"]:
            if bd["min"] <= row.churn_risk_score <= bd["max"]:
                return bd["name"]
        return "Low"

    df["risk_band"] = [band(r) for r in df.itertuples()]

    # ── CLV: real spend projected by expected remaining lifetime ──
    margin = 0.42
    retention = (1 - df["churn_risk_score"] / 100).clip(0.05, 0.95)
    df["clv_eur"] = (df["total_spend_eur"] * margin * (1 + retention * 2)).round(2)

    df["last_order_at"] = df["last_order_at"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    df["first_order_at"] = df["first_order_at"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    df["total_spend_eur"] = df["total_spend_eur"].round(2)
    df["avg_order_value_eur"] = df["avg_order_value_eur"].round(2)
    df["engagement_rate"] = df["engagement_rate"].round(4)
    df["click_rate"] = df["click_rate"].round(4)

    cols = ["customer_id", "is_customer", "churn_risk_score", "risk_band", "clv_eur", "nps_last",
            "total_orders", "total_spend_eur", "avg_order_value_eur",
            "days_since_last_order", "orders_90d", "orders_prev_90d",
            "sends", "opens", "clicks", "engagement_rate", "click_rate", "unsubscribed",
            "total_interactions", "negative_interactions", "unresolved_interactions",
            "first_order_at", "last_order_at"]
    return df[cols]


def relabel_lifecycle(cfg, customers, profile):
    """Lifecycle stage must AGREE with behaviour (the prototype's did not)."""
    at_risk = cfg["churn_model"]["at_risk_threshold"]
    p = profile.set_index("customer_id")
    stages = []
    for c in customers.itertuples():
        r = p.loc[c.customer_id]
        if r.total_orders == 0:
            stages.append("lead" if r.sends == 0 else "prospect")
        elif r.days_since_last_order > 365:
            stages.append("churned")
        elif r.churn_risk_score >= at_risk:
            stages.append("at_risk")
        else:
            stages.append("active")

    customers = customers.copy()
    customers["lifecycle_stage"] = stages
    customers["status"] = np.where(customers["lifecycle_stage"] == "churned", "churned", "active")
    return customers


# ══════════════════════════════════════════════════════════════════
# 5. Text corpus
# ══════════════════════════════════════════════════════════════════

def write_text_corpus(cfg, rng, customers, profile, campaigns):
    notes_dir = RAW / "text" / "customer_knowledge_notes"
    mails_dir = RAW / "text" / "email_bodies"
    notes_dir.mkdir(parents=True, exist_ok=True)
    mails_dir.mkdir(parents=True, exist_ok=True)

    p = profile.set_index("customer_id")
    sample = customers.sample(n=min(1500, len(customers)), random_state=SEED)
    for c in sample.itertuples():
        r = p.loc[c.customer_id]
        if r.churn_risk_score >= cfg["churn_model"]["at_risk_threshold"]:
            body = (f"Client mecontent du volume d'emails recu. Derniere commande il y a "
                    f"{int(r.days_since_last_order)} jours. Risque de perte eleve "
                    f"(score {int(r.churn_risk_score)}). Proposer un geste commercial et reduire la pression marketing.")
        elif r.total_orders == 0:
            body = "Prospect n'ayant pas encore commande. Nurturing en cours, pas de signal d'achat fort."
        else:
            body = (f"Client fidele, {int(r.total_orders)} commandes pour "
                    f"{r.total_spend_eur:.0f} EUR. Satisfaction correcte (NPS {int(r.nps_last)}).")
        (notes_dir / f"{c.customer_id}.txt").write_text(
            f"CUSTOMER_ID: {c.customer_id}\nDATE: {cfg['business']['reference_date']}\n\n{body}\n",
            encoding="utf-8")

    for camp in campaigns.itertuples():
        (mails_dir / f"{camp.campaign_id}.txt").write_text(
            f"CAMPAIGN_ID: {camp.campaign_id}\nSUBJECT: {camp.campaign_name}\n\n"
            f"Objectif: {camp.objective}. Decouvrez notre selection et profitez de nos offres.\n",
            encoding="utf-8")
    return len(sample), len(campaigns)


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

def write(df, folder, name):
    d = RAW / folder
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / f"{name}.csv", index=False, encoding="utf-8")
    print(f"   {folder}/{name}.csv: {len(df):,} rows")


def main():
    cfg = load_config()
    rng = _rng(cfg)
    print(f"Generating Customer 360 dataset (seed={SEED})")
    print(f"   storyline: {cfg['storyline']['culprit_campaign_id']} "
          f"'{cfg['storyline']['culprit_campaign_name']}' -> {cfg['storyline']['victim_segment_id']}\n")

    print("[1/5] CRM")
    accounts = build_accounts(cfg, rng)
    customers = build_customers(cfg, rng, accounts)
    segments = build_segments(cfg)
    cust_segments = assign_segments(cfg, rng, customers)

    print("[2/5] Marketing + catalogue")
    campaigns = build_campaigns(cfg, rng)
    assets = build_assets(cfg, rng, campaigns)
    audiences = build_audiences(cfg, rng, campaigns)
    products = build_products(cfg, rng)

    print("[3/5] Simulating behaviour (sends, events, orders)...")
    sends, events, orders, order_lines, returns, behaviour = simulate_behaviour(
        cfg, rng, customers, campaigns, audiences, cust_segments, products)

    print("[4/5] Deriving churn from behaviour")
    interactions = build_interactions(cfg, rng, customers, behaviour)
    profile = build_customer_profile(cfg, rng, customers, orders, events, sends, interactions, behaviour)
    customers = relabel_lifecycle(cfg, customers, profile)

    print("[5/5] Writing CSVs")
    write(accounts, "crm", "crm_accounts")
    write(customers, "crm", "crm_customers")
    write(segments, "crm", "crm_segments")
    write(cust_segments, "crm", "crm_customer_segments")
    write(interactions, "crm", "crm_interactions")
    write(profile, "crm", "crm_customer_profile")
    write(campaigns, "marketing", "marketing_campaigns")
    write(assets, "marketing", "marketing_assets")
    write(audiences, "marketing", "marketing_audiences")
    write(sends, "marketing", "marketing_sends")
    write(events, "marketing", "marketing_events")
    write(products, "commerce", "products")
    write(orders, "commerce", "orders")
    write(order_lines, "commerce", "order_lines")
    write(returns, "commerce", "returns")

    n_notes, n_mails = write_text_corpus(cfg, rng, customers, profile, campaigns)
    print(f"   text/customer_knowledge_notes: {n_notes:,} files")
    print(f"   text/email_bodies: {n_mails:,} files")

    # ── storyline check (printed so a demo run proves the signal exists) ──
    at_risk = cfg["churn_model"]["at_risk_threshold"]
    p = profile.set_index("customer_id")
    fat = [c for c, v in behaviour.items() if v.get("fatigued")]
    rest = [c for c in p.index if c not in set(fat)]
    print("\nStoryline check")
    print(f"   fatigued cohort (CAMP_007 burn) : {len(fat):,} customers")
    print(f"   mean churn score, fatigued      : {p.loc[fat, 'churn_risk_score'].mean():5.1f}")
    print(f"   mean churn score, everyone else : {p.loc[rest, 'churn_risk_score'].mean():5.1f}")
    print(f"   mean engagement, fatigued       : {p.loc[fat, 'engagement_rate'].mean():5.3f}")
    print(f"   mean engagement, everyone else  : {p.loc[rest, 'engagement_rate'].mean():5.3f}")
    n_risk = int((profile["churn_risk_score"] >= at_risk).sum())
    print(f"   customers at risk (>= {at_risk})        : {n_risk:,} / {len(profile):,} "
          f"({n_risk / len(profile) * 100:.1f}%)")
    risky = set(profile[profile["churn_risk_score"] >= at_risk]["customer_id"])
    share = len(risky & set(fat)) / max(1, len(risky))
    print(f"   share of at-risk explained by CAMP_007: {share * 100:.0f}%")
    print("\nDone.")


if __name__ == "__main__":
    main()
