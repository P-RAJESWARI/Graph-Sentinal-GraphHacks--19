import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents
from . import queries as Q
from .db import TENANTS, UnknownTenant, q, use_tenant
from .seed import seed

app = FastAPI(title="GraphSentinel AI", version="1.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Ask(BaseModel):
    question: str
    tenant: str = "acme"
    user: str = "demo-user"


class Confirm(BaseModel):
    deployment_id: str
    tenant: str = "acme"
    user: str = "demo-user"


class Approve(BaseModel):
    tenant: str = "acme"
    user: str = "demo-user"


@app.exception_handler(UnknownTenant)
def unknown_tenant(_, exc):
    return JSONResponse({"detail": f"unknown tenant {exc}; configured: {TENANTS}"}, status_code=404)


@app.on_event("startup")
def bootstrap():
    # Auto-seed each empty tenant graph so a fresh deploy works with zero manual steps.
    for t in TENANTS:
        use_tenant(t)
        try:
            if not q("MATCH (s:Service) RETURN count(s) AS c")[0]["c"]:
                seed(t)
        except Exception:
            seed(t)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health(tenant: str = "acme"):
    use_tenant(tenant)
    return {"ok": True, "tenant": tenant, "tenants": TENANTS, "nodes": q("MATCH (n) RETURN count(n) AS c")[0]["c"],
            "edges": q("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]}


@app.post("/api/seed")
def reseed(tenant: str = "acme"):
    return seed(tenant)


@app.get("/api/incidents")
def incidents(tenant: str = "acme"):
    use_tenant(tenant)
    return q(Q.OPEN_INCIDENTS)


@app.get("/api/overview")
def overview(tenant: str = "acme"):
    use_tenant(tenant)
    return {"nodes": q(Q.OVERVIEW_NODES), "edges": q(Q.OVERVIEW_EDGES)}


@app.post("/api/incidents/{incident_id}/investigate")
def investigate(incident_id: str, body: Ask | None = None):
    b = body or Ask(question="")
    try:
        return agents.investigate(incident_id, b.tenant, b.user, b.question)
    except KeyError:
        raise HTTPException(404, "unknown incident")


@app.post("/api/incidents/{incident_id}/confirm")
def confirm(incident_id: str, body: Confirm):
    try:
        return agents.confirm(incident_id, body.deployment_id, body.tenant, body.user)
    except KeyError as e:
        raise HTTPException(404, f"unknown {e}")


@app.post("/api/actions/{action_id}/approve")
def approve(action_id: str, body: Approve):
    try:
        return agents.approve(action_id, body.tenant, body.user)
    except KeyError:
        raise HTTPException(404, "unknown action")


@app.get("/api/memory")
def memory(tenant: str = "acme", user: str = "demo-user"):
    use_tenant(tenant)
    return {"episodes": agents.recall(user, tenant),
            "facts": q("MATCH (f:Fact {tenant:$t}) RETURN f.statement AS statement, f.confirmations AS confirmations "
                       "ORDER BY confirmations DESC", {"t": tenant})}


@app.post("/api/ask")
def ask(body: Ask):
    """Company-brain Q&A. Intent is routed to graph tools; every answer cites its graph sources."""
    use_tenant(body.tenant)
    text, low = body.question, body.question.lower()
    services = [r["name"] for r in q(Q.SERVICE_NAMES)]
    svc = next((s for s in services if s in low), None)
    inc = re.search(r"inc-\d+", low)
    if inc:
        return {"intent": "investigate", **agents.investigate(inc.group(0).upper(), body.tenant, body.user, text)}
    if any(w in low for w in ("remember", "earlier", "before", "history", "previous")):
        eps = agents.recall(body.user, body.tenant, svc or "")
        return {"intent": "memory", "answer": f"{len(eps)} earlier session(s) found.", "sources": eps}
    if not svc:
        return {"intent": "help", "answer": "Ask about a service (e.g. 'who owns payments-api?') or an incident "
                "(e.g. 'investigate INC-201').", "sources": []}
    if "own" in low:
        rows = q(Q.OWNERS, {"svc": svc})
        return {"intent": "owner", "answer": f"{svc} is owned by {rows[0]['team']}; on-call: "
                + ", ".join(r["person"] for r in rows if r["oncall"]), "sources": rows}
    if any(w in low for w in ("know", "expert")):
        rows = q(Q.EXPERTS, {"svc": svc})
        return {"intent": "expert", "answer": "Most experienced: " + ", ".join(
            f"{r['person']} ({r['incidents']} related incidents)" for r in rows), "sources": rows}
    if "depend" in low or "impact" in low or "blast" in low:
        rows = q(Q.BLAST_RADIUS, {"svc": svc})
        return {"intent": "blast_radius", "answer": f"{len(rows)} services depend on {svc} (directly or transitively): "
                + ", ".join(r["service"] for r in rows), "sources": rows}
    rows = q(Q.DECISIONS_FOR_SERVICE, {"svc": svc})
    return {"intent": "decisions", "answer": "; ".join(f"{r['id']}: {r['title']} (by {r['made_by']}, source {r['doc']})"
                                                       for r in rows) or "No decisions recorded.", "sources": rows}
