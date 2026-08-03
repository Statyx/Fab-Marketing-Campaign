"""
Customer 360 Portal — FastAPI backend (config-driven persona registry).

Each persona = one entry in AGENTS: its own report page(s), accent, welcome message
and suggested questions, all backed by the single dual-source Data Agent
(ontology for relationships/RCA, semantic model for every number).
The frontend auto-discovers personas from /api/agents — adding one is a dict entry.

Uses the OpenAI Assistants API shape exposed by Fabric Data Agents, and
AzureCliCredential (your `az login`) — no service principal needed.
"""

import os, sys, ast, asyncio, httpx, logging, json, base64, re, threading, time as _time, traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# trace_source sits next to this file. Whether it is importable depends on HOW uvicorn
# was launched - `backend.main:app` from portal/ puts portal/ on sys.path, not
# portal/backend/ - and the portal then dies on startup with ModuleNotFoundError while
# the code itself is fine. Pinning the directory makes the import launch-independent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_source  # noqa: E402

# ── Config ───────────────────────────────────────────────────
# src/state.json is the source of truth, so the portal can never point at a stale or
# deleted item. Env vars override it; there are no hardcoded IDs.

_SRC = Path(__file__).resolve().parents[2] / "src"


def _state() -> dict:
    try:
        return json.loads((_SRC / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _config_value(key: str, default: str) -> str:
    """Read a scalar from src/config.yaml with a small regex (avoids a pyyaml dependency)."""
    try:
        text = (_SRC / "config.yaml").read_text(encoding="utf-8")
        m = re.search(rf'^\s*{key}:\s*"([^"]+)"', text, re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return default


def _config_int(key: str, default: int) -> int:
    """Read an unquoted integer scalar from src/config.yaml (volumes block)."""
    try:
        text = (_SRC / "config.yaml").read_text(encoding="utf-8")
        m = re.search(rf'^\s*{key}:\s*(\d+)', text, re.M)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return default


_ST = _state()
WORKSPACE_ID = os.getenv("WORKSPACE_ID") or _ST.get("workspace_id", "")
REPORT_ID = os.getenv("REPORT_ID") or _ST.get("report_id", "")
DATASET_ID = os.getenv("DATASET_ID") or _ST.get("semantic_model_id", "")
DATA_AGENT_ID = os.getenv("DATA_AGENT_ID") or _ST.get("data_agent_id", "")
ONTOLOGY_ID = os.getenv("ONTOLOGY_ID") or _ST.get("ontology_id", "")

WORKSPACE_NAME = _config_value("workspace_name", "CDR - Marketing Campaign")
SM_NAME = _config_value("semantic_model_name", "SM_Marketing_Analytics")
RPT_NAME = _config_value("report_name", "RPT_Marketing_Churn")
CULPRIT = _config_value("culprit_campaign_name", "Black Friday Blast")
# Landing-page counters come from the generator's own volumes, so they can never
# drift from the data that was actually generated.
DEMO_COUNTS = {"customers": _config_int("customers", 0),
               "campaigns": _config_int("campaigns", 0),
               "segments": _config_int("segments", 0)}

STAGE = os.getenv("AGENT_STAGE", "production")
API_VERSION = "2024-02-15-preview"
PBI_BASE = "https://api.powerbi.com/v1.0/myorg"

# ── The graph, read from its own deployer ────────────────────
# src/deploy_ontology.py is the only source of truth for the shape of the graph.
# Parsed with ast, deliberately NOT imported: importing the deployer pulls in requests
# and yaml and runs module-level code, so the portal would fail to start for a reason
# that has nothing to do with the portal. A copy of the lists here would drift silently
# the first time an entity is added - and the audience is looking at this panel.
ONT_NAME = "ONT_Customer360"


def _ontology_graph() -> dict:
    try:
        tree = ast.parse((_SRC / "deploy_ontology.py").read_text(encoding="utf-8"))
    except Exception:
        return {"entities": [], "relationships": []}
    found: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", "")
            if name in ("ENTITIES", "RELATIONSHIPS"):
                try:
                    found[name] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return {
        "name": ONT_NAME,
        "id": ONTOLOGY_ID,
        "entities": [{"name": e[0], "table": e[1], "properties": len(e[3])}
                     for e in found.get("ENTITIES", [])],
        "relationships": [{"name": r[0], "from": r[1], "to": r[2]}
                          for r in found.get("RELATIONSHIPS", [])],
    }


ONT_GRAPH = _ontology_graph()

# ── Persona registry ─────────────────────────────────────────
# 4 personas, all backed by the single Marketing_Churn_Agent, mapped 1:1 onto the pages
# of RPT_Marketing_Churn. `reportPages` is matched against the Power BI page displayName
# by the frontend, so it must stay in sync with deploy_report.py.
_DS = [{"id": DATASET_ID, "name": SM_NAME,
        "scope": "Churn, engagement email, campagnes, commandes, attribution"}]
_GR = [{"id": ONTOLOGY_ID, "name": ONT_NAME,
        "scope": "Qui est relie a quoi : campagne, segment, compte, commande, produit"}]
_RP = [{"id": REPORT_ID, "name": RPT_NAME}]

# Every suggestion declares the source it is EXPECTED to use, so the audience can see the
# routing before the question is even sent. "Expected", never "guaranteed": the agent
# decides its own routing at runtime and the badge under the answer is the only statement
# of what actually happened. The `ontology` ones are the 8 phrasings that were probed live
# on 3 Aug 2026 and reached the graph 8/8 - a question that merely *looks* relational is
# not enough, the first 20 canned questions all looked fine and 0 of them left the model.
ONT, SEM = "ontology", "model"

AGENTS: dict[str, dict] = {
    "direction": {
        "id": DATA_AGENT_ID,
        "name": "Direction",
        "description": "Pilotage global : valeur du portefeuille, exposition a l'attrition, NPS",
        "icon": "🎯",
        "accent": "#00008F",
        "datasets": _DS,
        "graphs": _GR,
        "reports": _RP,
        "reportPages": ["Direction"],
        "welcome": ("Bonjour, je suis l'assistant de pilotage de la relation client. "
                    "Interrogez-moi sur le chiffre d'affaires, la valeur du portefeuille, "
                    "la part de clients a risque et la sante de la relation."),
        "suggestions": [
            {"q": "Quelle part de la base client est a risque d'attrition ?", "src": SEM},
            {"q": "Combien de valeur vie client est exposee au churn ?", "src": SEM},
            {"q": "Quel est le chiffre d'affaires total ?", "src": SEM},
            {"q": "Quel est le score d'attrition moyen par etape du cycle de vie ?", "src": SEM},
            {"q": "Quel est le NPS moyen de la base ?", "src": SEM},
            {"q": f"Quels comptes B2B regroupent le plus de clients touches par la campagne {CULPRIT} ?",
             "src": ONT},
        ],
    },
    "retention": {
        "id": DATA_AGENT_ID,
        "name": "Retention",
        "description": "Detection : la cohorte a risque, ses signaux et les clients a rappeler",
        "icon": "🛟",
        "accent": "#027180",
        "datasets": _DS,
        "graphs": _GR,
        "reports": _RP,
        "reportPages": ["Retention"],
        "welcome": ("Bonjour, je suis l'assistant retention. Posez-moi vos questions sur les "
                    "clients a risque, leur recence, leur engagement, leurs desabonnements "
                    "et la friction support."),
        "suggestions": [
            {"q": "Combien de clients sont a risque et pour quelle valeur ?", "src": SEM},
            {"q": "Quels clients dois-je rappeler en priorite ?", "src": SEM},
            {"q": "Quelle est la recence moyenne des clients a risque ?", "src": SEM},
            {"q": "Combien de clients se sont desabonnes des emails ?", "src": SEM},
            {"q": "Les interactions support negatives augmentent-elles le risque ?", "src": SEM},
            {"q": "Quels comptes B2B concentrent le plus d interactions support negatives ?",
             "src": ONT},
            {"q": "Quels clients a risque appartiennent au meme compte B2B ?", "src": ONT},
        ],
    },
    "marketing": {
        "id": DATA_AGENT_ID,
        "name": "Marketing",
        "description": "Diagnostic : pression email par campagne, desabonnements, engagement",
        "icon": "📣",
        "accent": "#896610",
        "datasets": _DS,
        "graphs": _GR,
        "reports": _RP,
        "reportPages": ["Marketing"],
        "welcome": ("Bonjour, je suis l'assistant marketing. Interrogez-moi sur la pression "
                    "commerciale par campagne, les taux d'ouverture, de clic et de "
                    "desabonnement, et la cause racine de l'attrition."),
        "suggestions": [
            {"q": "Quelle campagne envoie le plus d'emails par client ?", "src": SEM},
            {"q": f"Pourquoi la campagne « {CULPRIT} » genere-t-elle autant de desabonnements ?",
             "src": SEM},
            {"q": "Compare les taux d'ouverture entre campagnes", "src": SEM},
            {"q": "Quel est le taux de desabonnement global ?", "src": SEM},
            {"q": "Quel segment concentre le plus d'envois et quel est son score de churn ?",
             "src": SEM},
            {"q": f"Quels segments la campagne {CULPRIT} ciblait-elle et quels segments "
                  f"a-t-elle reellement touches ?", "src": ONT},
            {"q": f"Quels objets d email ont ete utilises par la campagne {CULPRIT} ?", "src": ONT},
        ],
    },
    "commerce": {
        "id": DATA_AGENT_ID,
        "name": "Commerce",
        "description": "Impact business : chiffre d'affaires, panier, attribution, retours",
        "icon": "🛒",
        "accent": "#863C41",
        "datasets": _DS,
        "graphs": _GR,
        "reports": _RP,
        "reportPages": ["Commerce"],
        "welcome": ("Bonjour, je suis l'assistant commerce. Posez-moi vos questions sur le "
                    "chiffre d'affaires, le panier moyen, les categories de produits, "
                    "l'attribution des campagnes et les retours."),
        "suggestions": [
            {"q": "Quelle categorie de produit genere le plus de chiffre d'affaires ?", "src": SEM},
            {"q": "Quel est le panier moyen par canal de vente ?", "src": SEM},
            {"q": "Quelle part du chiffre d'affaires est attribuee aux campagnes ?", "src": SEM},
            {"q": "Quels sont les principaux motifs de retour ?", "src": SEM},
            {"q": f"Quels produits ont ete achetes par les clients touches par {CULPRIT} ?",
             "src": ONT},
            {"q": "Quelles categories de produits les clients du segment High Value achetent-ils ?",
             "src": ONT},
        ],
    },
}


def _agent_base(agent_id: str) -> str:
    return (f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
            f"/dataAgents/{agent_id}/aiassistant/openai")


# ── Auth ─────────────────────────────────────────────────────
from azure.identity import AzureCliCredential

credential = AzureCliCredential(process_timeout=30)  # the 10s default is too tight on Windows
log = logging.getLogger("portal")

# Token cache: repeated `az` subprocess calls are slow and known to hang.
_token_cache: dict[str, tuple[str, float]] = {}   # scope -> (token, expires_on)
_token_lock = threading.Lock()                    # prevent a refresh stampede


def _cached_token(scope: str, force: bool = False) -> str:
    cached = _token_cache.get(scope)
    if not force and cached and cached[1] > _time.time() + 300:   # 5 min buffer
        return cached[0]
    with _token_lock:
        cached = _token_cache.get(scope)          # re-check inside the lock
        if not force and cached and cached[1] > _time.time() + 300:
            return cached[0]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                log.warning(f"Acquiring token for {scope} (attempt {attempt + 1})...")
                result = credential.get_token(scope)
                _token_cache[scope] = (result.token, result.expires_on)
                return result.token
            except Exception as e:
                last_err = e
                log.warning(f"Token attempt {attempt + 1} failed: {e}")
                _time.sleep(0.5 * (attempt + 1))
        if cached and cached[1] > _time.time():   # fall back on a still-valid token
            log.warning(f"Token refresh failed after retries, reusing cached token: {last_err}")
            return cached[0]
        raise last_err  # type: ignore[misc]


def fabric_token() -> str:
    return _cached_token("https://api.fabric.microsoft.com/.default")


def pbi_token() -> str:
    return _cached_token("https://analysis.windows.net/powerbi/api/.default")


def fabric_headers():
    return {"Authorization": f"Bearer {fabric_token()}", "Content-Type": "application/json"}


def pbi_headers():
    return {"Authorization": f"Bearer {pbi_token()}", "Content-Type": "application/json"}


def agent_params():
    return {"stage": STAGE, "api-version": API_VERSION}


# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="Customer 360 Portal API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def log_unhandled_errors(request: Request, call_next):
    """Log any unhandled exception with its traceback and return a JSON 502,
    instead of an opaque 502 with nothing in the terminal."""
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"UNHANDLED {request.method} {request.url.path}: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e), "path": request.url.path,
                             "hint": "Try POST /api/admin/refresh-tokens"}, status_code=502)


@app.get("/api/health")
async def health():
    """Liveness + token freshness. Hit this first when you suspect a 502."""
    status: dict = {"ok": True, "tokens": {},
                    "workspace": WORKSPACE_ID,
                    "report": REPORT_ID,
                    "agents": {k: v["name"] for k, v in AGENTS.items()}}
    for label, scope in [("fabric", "https://api.fabric.microsoft.com/.default"),
                         ("powerbi", "https://analysis.windows.net/powerbi/api/.default")]:
        try:
            _cached_token(scope)
            cached = _token_cache.get(scope)
            status["tokens"][label] = {"ok": True,
                                       "expires_in_s": int(cached[1] - _time.time()) if cached else 0}
        except Exception as e:
            status["ok"] = False
            status["tokens"][label] = {"ok": False, "error": str(e)}
    status["tenantId"] = _extract_tenant_id()
    if not REPORT_ID:
        status["ok"] = False
        status["error"] = "report_id missing from src/state.json — run deploy_report.py"
    return JSONResponse(status, status_code=200 if status["ok"] else 503)


@app.post("/api/admin/refresh-tokens")
async def refresh_tokens():
    """Force-refresh both tokens — use this instead of restarting the server."""
    _token_cache.clear()
    out: dict = {}
    for label, scope in [("fabric", "https://api.fabric.microsoft.com/.default"),
                         ("powerbi", "https://analysis.windows.net/powerbi/api/.default")]:
        try:
            _cached_token(scope, force=True)
            out[label] = "refreshed"
        except Exception as e:
            out[label] = f"FAILED: {e}"
    return out


@app.on_event("startup")
async def _prewarm():
    """Pre-warm tokens so the first request doesn't pay the az-cli latency."""
    try:
        fabric_token()
        pbi_token()
        log.warning("Tokens pre-warmed OK")
    except Exception as e:
        log.warning(f"Token pre-warm failed (will retry on first request): {e}")


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


# ── Models ───────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


class ToolTrace(BaseModel):
    tool: str
    arguments: str = ""
    output: str = ""


class ChatResponse(BaseModel):
    answer: str
    steps: list[str] = []
    queryTrace: list[ToolTrace] = []
    followUps: list[str] = []
    source: str = ""            # "ontology" | "semantic_model" | "" when nothing ran
    sourceName: str = ""        # ONT_Customer360 / SM_Marketing_Analytics
    queryLanguage: str = ""     # "GQL" | "DAX"
    generatedQuery: str = ""    # the query the agent actually wrote


# ── Persona registry endpoint ────────────────────────────────
def _extract_tenant_id() -> str:
    """Decode the PBI token to read the current tenant ID (claims only, no secret)."""
    try:
        payload = pbi_token().split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("tid", "")
    except Exception:
        return ""


@app.get("/api/agents")
async def list_agents():
    """Persona registry for the frontend (public IDs only).
    `_meta` carries the cross-persona context (tenant, workspace, ontology)."""
    out: dict = {"_meta": {"tenantId": _extract_tenant_id(),
                           "workspaceId": WORKSPACE_ID,
                           "workspaceName": WORKSPACE_NAME,
                           "ontologyId": ONTOLOGY_ID,
                           "demo": DEMO_COUNTS}}
    for key, cfg in AGENTS.items():
        out[key] = {"name": cfg["name"], "description": cfg["description"],
                    "icon": cfg["icon"], "accent": cfg["accent"], "agentId": cfg["id"],
                    "datasets": cfg.get("datasets", []), "graphs": cfg.get("graphs", []),
                    "reports": cfg.get("reports", []),
                    "reportPages": cfg["reportPages"], "suggestions": cfg["suggestions"],
                    "welcome": cfg.get("welcome", "")}
    out["_meta"]["graph"] = ONT_GRAPH
    return out


# ── Follow-up suggestions ────────────────────────────────────
# Keyword -> follow-ups, so the conversation keeps walking the demo arc
# detecter -> diagnostiquer -> quantifier -> agir instead of dead-ending.
_FOLLOWUP_TEMPLATES = {
    "direction": {
        r"risque|churn|attrition|partir": [
            "Combien de valeur vie client est exposee a ce risque ?",
            "Quelle etape du cycle de vie concentre le risque ?",
            "Quelle campagne est a l'origine de ce risque ?",
        ],
        r"chiffre|revenu|\bca\b|vente": [
            "Quelle part du chiffre d'affaires est attribuee aux campagnes ?",
            "Quel est le panier moyen ?",
        ],
        r"nps|satisfaction|promoteur|detracteur": [
            "Les detracteurs sont-ils plus a risque d'attrition ?",
            "Combien d'interactions support negatives non resolues ?",
        ],
    },
    "retention": {
        r"risque|cohorte|score|attrition": [
            "Quels clients dois-je rappeler en priorite ?",
            "Quelle part de cette cohorte a recu la campagne coupable ?",
            "Combien de chiffre d'affaires historique est expose ?",
        ],
        r"recence|inactif|derniere commande|dormant": [
            "Depuis combien de temps ces clients n'ont-ils pas commande ?",
            "Combien de clients sont deja consideres comme perdus ?",
        ],
        r"desabonn|unsub|opt.?out|consentement": [
            "Combien de clients desabonnes sont encore des acheteurs ?",
            "Quelle campagne a declenche ces desabonnements ?",
        ],
        r"support|interaction|reclamation|negatif": [
            "Combien d'interactions negatives restent non resolues ?",
            "Ces clients ont-ils un score d'attrition plus eleve ?",
        ],
    },
    "marketing": {
        r"pression|envoi|email|sollicit|fatigue": [
            "Quelle campagne envoie le plus d'emails par client ?",
            "Quel segment a subi cette sur-sollicitation ?",
            "Combien de desabonnements cette campagne a-t-elle generes ?",
        ],
        r"ouverture|clic|engagement|taux": [
            "Comment le taux d'ouverture se compare-t-il entre campagnes ?",
            "Quel est le taux de desabonnement par campagne ?",
        ],
        r"campagne|budget|objectif|roi": [
            "Quel est le ROI de chaque campagne ?",
            "Quel budget a ete investi par objectif ?",
        ],
        r"segment|cible|audience": [
            "Quel segment a le plus souffert ?",
            "Combien de clients de ce segment sont maintenant a risque ?",
        ],
    },
    "commerce": {
        r"chiffre|revenu|\bca\b|panier": [
            "Quel est le panier moyen par canal de vente ?",
            "Quelle categorie de produit contribue le plus ?",
        ],
        r"attribution|campagne|roi": [
            "Quel est le ROI des campagnes ?",
            "Quelle part des commandes est attribuee a une campagne ?",
        ],
        r"retour|remboursement|motif": [
            "Quels sont les principaux motifs de retour ?",
            "Le taux de retour varie-t-il par categorie ?",
        ],
        r"commande|frequence|volume": [
            "Comment le volume de commandes se repartit-il par canal ?",
            "Les clients a risque ont-ils arrete de commander ?",
        ],
    },
}

_UNIVERSAL_FOLLOWUPS = {
    "direction": [
        "Donne-moi une vue d'ensemble de la relation client",
        "Quels indicateurs dois-je surveiller en priorite ?",
        "Ou faut-il concentrer les efforts ?",
    ],
    "retention": [
        "Combien de clients sont a risque et pour quelle valeur ?",
        "Quels clients dois-je rappeler en priorite ?",
        "Quelle est la cause racine de ce risque ?",
    ],
    "marketing": [
        "Quelle campagne envoie le plus d'emails par client ?",
        "Quel est le taux de desabonnement par campagne ?",
        "Quel segment concentre le plus d'envois et quel est son score de churn ?",
    ],
    "commerce": [
        "Quelle categorie genere le plus de chiffre d'affaires ?",
        "Quel est le ROI des campagnes ?",
        "Quel est le panier moyen par canal ?",
    ],
}


def _generate_followups(agent_key: str, question: str, answer: str) -> list[str]:
    """Three contextual follow-ups derived from the question + the agent's answer."""
    combined = (question + " " + answer).lower()
    q_lower = question.lower()
    seen: set[str] = set()
    unique: list[str] = []

    def _push(items):
        for c in items:
            if c not in seen and c.lower() != q_lower:
                seen.add(c)
                unique.append(c)

    for pattern, suggestions in _FOLLOWUP_TEMPLATES.get(agent_key, {}).items():
        if re.search(pattern, combined):
            _push(suggestions)
    if len(unique) < 3:
        _push(_UNIVERSAL_FOLLOWUPS.get(agent_key, []))
    return unique[:3]


# ── Data Agent chat (OpenAI Assistants API) ──────────────────
def _raise_if_throttled(resp):
    """Fabric throttles the agent per capacity and answers 429 RequestBlocked.

    Left as a bare 502 this reads as "the portal is broken" in the middle of a
    demo. It is not: it is a cool-down, it states when it lifts, and the only
    cure is to wait. Surface that instead of a gateway error.
    """
    if resp.status_code != 429:
        return
    detail = "L'agent est temporairement limité par Fabric (trop de questions rapprochées)."
    try:
        msg = resp.json().get("message", "")
        if "until:" in msg:
            detail += f" Réessayez après {msg.split('until:')[1].strip()}."
    except Exception:
        pass
    raise HTTPException(429, detail)


@app.post("/api/agents/{agent_key}/chat", response_model=ChatResponse)
async def agent_chat(agent_key: str, req: ChatRequest):
    """Ask a persona's Data Agent a question and return the answer + tool trace."""
    if agent_key not in AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent_key}")
    if not DATA_AGENT_ID:
        raise HTTPException(503, "data_agent_id missing from src/state.json — run deploy_data_agent.py")

    base = _agent_base(AGENTS[agent_key]["id"])

    async with httpx.AsyncClient(timeout=120) as client:
        headers = fabric_headers()
        params = agent_params()

        asst_resp = await client.post(f"{base}/assistants", headers=headers, params=params,
                                      json={"model": "irrelevant"})
        _raise_if_throttled(asst_resp)
        if asst_resp.status_code not in (200, 201):
            raise HTTPException(502, f"Assistant creation failed: {asst_resp.status_code} {asst_resp.text[:200]}")
        assistant_id = asst_resp.json()["id"]

        async def _open_thread() -> str:
            resp = await client.post(f"{base}/threads", headers=headers, params=params, json={})
            _raise_if_throttled(resp)
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"Thread creation failed: {resp.status_code} {resp.text[:200]}")
            return resp.json()["id"]

        async def _attempt(thread_id: str):
            """Run the question once on this thread. Returns (answer, steps, trace, status)."""
            msg_resp = await client.post(f"{base}/threads/{thread_id}/messages",
                                         headers=headers, params=params,
                                         json={"role": "user", "content": req.message})
            if msg_resp.status_code not in (200, 201):
                raise HTTPException(502, f"Message send failed: {msg_resp.status_code} {msg_resp.text[:200]}")

            run_resp = await client.post(f"{base}/threads/{thread_id}/runs",
                                         headers=headers, params=params,
                                         json={"assistant_id": assistant_id})
            if run_resp.status_code not in (200, 201):
                raise HTTPException(502, f"Run creation failed: {run_resp.status_code}")
            run_id = run_resp.json()["id"]

            run_status = ""
            for _ in range(60):                    # up to ~2 min
                await asyncio.sleep(2)
                status_resp = await client.get(f"{base}/threads/{thread_id}/runs/{run_id}",
                                               headers=fabric_headers(), params=params)
                if status_resp.status_code != 200:
                    continue
                run_status = status_resp.json().get("status", "")
                if run_status in ("completed", "failed", "cancelled", "expired"):
                    break

            msgs_resp = await client.get(f"{base}/threads/{thread_id}/messages",
                                         headers=fabric_headers(), params=params)
            answer = ""
            if msgs_resp.status_code == 200:
                for msg in msgs_resp.json().get("data", []):
                    # POST /threads returns ONE sticky thread per agent, reused by every
                    # caller, so this list holds other questions' answers too. Proven
                    # 3 Aug 2026 - two threads created back to back came back as
                    # thread_z2l0nmctAMLMaFU3rDTpnpOq both times, and a brand-new thread
                    # already listed 20 messages from earlier portal calls.
                    # Taking the newest assistant message therefore answers the wrong
                    # question whenever our own run produced none: a failed run served a
                    # confident, well-formed answer about a customer nobody had asked
                    # about. run_id is the only field that ties a message to this run.
                    if msg.get("role") == "assistant" and msg.get("run_id") == run_id:
                        for content in msg.get("content", []):
                            if content.get("type") == "text":
                                answer += content["text"].get("value", "")
                        break

            steps, query_trace = [], []
            steps_resp = await client.get(f"{base}/threads/{thread_id}/runs/{run_id}/steps",
                                          headers=fabric_headers(), params=params)
            if steps_resp.status_code == 200:
                for step in steps_resp.json().get("data", []):
                    for tc in step.get("step_details", {}).get("tool_calls", []):
                        fn_obj = tc.get("function", {})
                        fn = fn_obj.get("name", "")
                        if fn:
                            steps.append(fn)
                            query_trace.append(ToolTrace(tool=fn,
                                                         arguments=fn_obj.get("arguments", ""),
                                                         output=(fn_obj.get("output", "") or "")[:5000]))
            return answer, steps, query_trace, run_status

        # That sticky thread eventually reaches a state where EVERY run on it dies in ~2s
        # with {"code":"server_error"} before a single tool call - both datasources, any
        # instruction text. Measured 3 Aug 2026: 0/8 novel questions with two different
        # instruction sets, while the semantic model still answered DAX directly (HTTP 200)
        # on an Active F16, and a throwaway agent built from the same definition answered
        # first time - on a thread of its own. DELETE /threads/{id} is the cure: the next
        # POST hands back a genuinely new id and the agent recovers (3/3 right after).
        # So: purge, then ONE retry. Not on every call - deleting a thread another persona
        # is mid-run on would break that run, which is why this is a recovery path only.
        thread_id = await _open_thread()
        log.warning(f"[{agent_key}] Q: {req.message[:80]}")
        answer, steps, query_trace, run_status = await _attempt(thread_id)

        if not answer:
            log.warning(f"[{agent_key}] run {run_status or 'inconnu'} - purge du fil {thread_id}, 1 nouvel essai")
            await client.delete(f"{base}/threads/{thread_id}", headers=headers, params=params)
            thread_id = await _open_thread()
            answer, steps, query_trace, run_status = await _attempt(thread_id)

        if not answer:
            raise HTTPException(504, "L'agent n'a pas produit de réponse pour cette question "
                                     f"(statut du run : {run_status or 'inconnu'}). Reposez la question.")

        log.warning(f"[{agent_key}] DONE len={len(answer)} status={run_status} steps={steps}")
        src = trace_source.describe([t.model_dump() for t in query_trace])
        return ChatResponse(answer=answer, steps=steps, queryTrace=query_trace,
                            followUps=_generate_followups(agent_key, req.message, answer),
                            **src)


# ── Power BI embed (user-owns-data) ──────────────────────────
@app.get("/api/embed-token")
async def embed_token():
    """Report embed URL + the signed-in user's Power BI access token."""
    if not REPORT_ID:
        raise HTTPException(503, "report_id missing from src/state.json — run deploy_report.py")
    async with httpx.AsyncClient(timeout=30) as client:
        report_resp = await client.get(f"{PBI_BASE}/groups/{WORKSPACE_ID}/reports/{REPORT_ID}",
                                       headers=pbi_headers())
        if report_resp.status_code != 200:
            raise HTTPException(502, f"Report fetch failed: {report_resp.status_code}")
        return {"token": pbi_token(), "tokenType": "Aad",
                "embedUrl": report_resp.json().get("embedUrl", ""),
                "reportId": REPORT_ID, "tenantId": _extract_tenant_id()}
