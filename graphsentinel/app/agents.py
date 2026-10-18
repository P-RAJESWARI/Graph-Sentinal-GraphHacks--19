"""Three cooperating agents. All context, memory and hand-offs live in FalkorDB.

TriageAgent  -> (Task node in graph) -> RootCauseAgent -> (Task node) -> RemediationAgent
Every step appends to a `trace` so the UI can show how each answer was reached.
"""
import json
import time
import uuid

from . import queries as Q
from .db import q, run, use_tenant

WINDOW = 24 * 3600


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _handoff(frm: str, to: str, incident: str, tenant: str, payload: dict) -> None:
    run(Q.TASK_HANDOFF, {"from": frm, "to": to, "id": _id("T"), "tenant": tenant, "incident": incident,
                         "ts": int(time.time() * 1000), "payload": json.dumps(payload)})


def _read_task(to: str, incident: str, tenant: str) -> dict:
    rows = q(Q.TASK_READ, {"to": to, "incident": incident, "tenant": tenant})
    return json.loads(rows[0]["payload"]) if rows else {}


# ───────────────────────────── agent 1 ─────────────────────────────
def triage(incident_id: str, tenant: str, trace: list) -> dict:
    inc = q(Q.INCIDENT, {"id": incident_id})
    if not inc:
        raise KeyError(incident_id)
    inc = inc[0]
    blast = q(Q.BLAST_RADIUS, {"svc": inc["service"]})
    trace.append({"agent": "TriageAgent", "tool": "BLAST_RADIUS (multi-hop DEPENDS_ON*1..3)",
                  "result": f"{len(blast)} upstream services impacted: " + ", ".join(b["service"] for b in blast)})
    try:
        pr = {r["service"]: r["score"] for r in q(Q.PAGERANK)}
        trace.append({"agent": "TriageAgent", "tool": "algo.pageRank(Service, DEPENDS_ON)",
                      "result": f"{inc['service']} criticality score {pr.get(inc['service'], 0):.3f}"})
    except Exception:
        pr = {}
    payload = {"incident": inc, "blast": blast, "criticality": pr.get(inc["service"], 0)}
    _handoff("TriageAgent", "RootCauseAgent", incident_id, tenant, payload)
    trace.append({"agent": "TriageAgent", "tool": "handoff", "result": "Task node written to graph -> RootCauseAgent"})
    return payload


# ───────────────────────────── agent 2 ─────────────────────────────
def root_cause(incident_id: str, tenant: str, trace: list) -> dict:
    ctx = _read_task("RootCauseAgent", incident_id, tenant)  # shared state read from the graph
    inc = ctx["incident"]
    trace.append({"agent": "RootCauseAgent", "tool": "TASK_READ", "result": "Received triage context from graph"})

    cands = q(Q.CANDIDATES, {"id": incident_id, "window": WINDOW})
    trace.append({"agent": "RootCauseAgent", "tool": "CANDIDATES (multi-hop path + deployments in 24h)",
                  "result": f"{len(cands)} recent deployments reachable from {inc['service']}"})
    similar = q(Q.SIMILAR, {"id": incident_id})
    trace.append({"agent": "RootCauseAgent", "tool": "SIMILAR (shared Symptom nodes)",
                  "result": f"{len(similar)} similar past incidents: " + ", ".join(s["incident"] for s in similar)})
    facts = q(Q.FACTS_FOR_CANDIDATES, {"tenant": tenant, "svcs": list({c["service"] for c in cands}) or [""],
                                       "symptoms": inc["symptoms"]})
    fact_by_svc = {f["service"]: f for f in facts}
    if facts:
        trace.append({"agent": "RootCauseAgent", "tool": "FACTS_FOR_CANDIDATES (semantic memory)",
                      "result": "; ".join(f"{f['service']}: {f['confirmations']} prior confirmation(s)" for f in facts)})

    ranked = []
    for c in cands:
        age_h = (c["incident_ts"] - c["ts"]) / 3600
        s_recency = 40 * max(0.0, 1 - age_h / 24)
        s_prox = 30 / (1 + c["hops"])
        s_hist, why_hist = 0.0, []
        for s in similar:
            if s["cause_service"] == c["service"]:
                s_hist += 25 * s["similarity"]
                why_hist.append(f"{s['incident']} had the same service as root cause ({s['similarity']:.0%} symptom overlap)")
            if s["cause_change_type"] == c["change_type"]:
                s_hist += 5 * s["similarity"]
        s_hist = min(s_hist, 30)
        f = fact_by_svc.get(c["service"])
        s_mem = min(20, 6 * f["confirmations"]) if f else 0
        score = s_recency + s_prox + s_hist + s_mem
        ranked.append({
            **{k: c[k] for k in ("service", "hops", "path", "dep_id", "version", "change_type", "summary", "author")},
            "age_hours": round(age_h, 1), "score": round(score, 1),
            "breakdown": {"recency": round(s_recency, 1), "proximity": round(s_prox, 1),
                          "history": round(s_hist, 1), "memory": round(s_mem, 1)},
            "reasons": [f"Deployed {age_h:.1f}h before the incident", f"{c['hops']} hop(s) from {inc['service']}: "
                        + " -> ".join(c["path"])] + why_hist
                       + ([f"Learned memory: {f['statements'][0]} (confirmed {f['confirmations']}x)"] if f else []),
        })
    ranked.sort(key=lambda r: r["score"], reverse=True)
    top = ranked[0] if ranked else None
    if top and top["service"] != inc["service"]:
        try:
            sp = q(Q.SHORTEST_PATH, {"src": inc["service"], "dst": top["service"]})
        except Exception:
            sp = []
        if sp:
            top["shortest_path"] = sp[0]["path"]
            top["path"] = sp[0]["path"]  # evidence path = shortest dependency chain
            top["hops"] = sp[0]["hops"]
            trace.append({"agent": "RootCauseAgent", "tool": "shortestPath (BFS over DEPENDS_ON)",
                          "result": " -> ".join(sp[0]["path"]) + f" ({sp[0]['hops']} hops)"})
    if top:
        top["confidence"] = round(min(top["score"] / 100, 0.99), 2)  # score out of 100
        trace.append({"agent": "RootCauseAgent", "tool": "score(recency+proximity+history+memory)",
                      "result": f"Top suspect {top['dep_id']} on {top['service']} score {top['score']}"})
    out = {"ranked": ranked[:5], "similar": similar, "top": top, "incident": inc, "blast": ctx["blast"]}
    _handoff("RootCauseAgent", "RemediationAgent", incident_id, tenant,
             {"top": top, "similar": similar, "incident": inc})
    trace.append({"agent": "RootCauseAgent", "tool": "handoff", "result": "Task node written to graph -> RemediationAgent"})
    return out


# ───────────────────────────── agent 3 ─────────────────────────────
def remediation(incident_id: str, tenant: str, trace: list) -> dict:
    ctx = _read_task("RemediationAgent", incident_id, tenant)
    top, inc, similar = ctx["top"], ctx["incident"], ctx["similar"]
    trace.append({"agent": "RemediationAgent", "tool": "TASK_READ", "result": "Received root-cause context from graph"})
    if not top:
        return {"summary": "No candidate deployment found; escalate to SRE."}
    owners = q(Q.OWNERS, {"svc": top["service"]})
    experts = q(Q.EXPERTS, {"svc": top["service"]})
    decisions = q(Q.DECISIONS_FOR_SERVICE, {"svc": top["service"]})
    docs = q(Q.DOCS_FOR_SERVICES, {"svcs": list(dict.fromkeys(top["path"]))})
    proc = q(Q.PROCEDURE_FOR_SYMPTOMS, {"symptoms": inc["symptoms"]})
    trace.append({"agent": "RemediationAgent", "tool": "OWNERS / EXPERTS / DECISIONS / DOCS / PROCEDURE",
                  "result": f"{len(owners)} owners, {len(experts)} experts, {len(decisions)} decisions, "
                            f"{len(docs)} docs, runbook {proc[0]['id'] if proc else 'none'}"})
    oncall = next((o for o in owners if o["oncall"]), owners[0] if owners else None)
    action_id = _id("ACT")
    run(Q.ACTION_CREATE, {"dep": top["dep_id"], "id": action_id, "tenant": tenant,
                          "ts": int(time.time() * 1000), "incident": incident_id})
    trace.append({"agent": "RemediationAgent", "tool": "ACTION_CREATE",
                  "result": f"Proposed rollback of {top['dep_id']} ({action_id}) - awaiting human approval"})
    return {
        "team": oncall["team"] if oncall else None,
        "page": oncall["person"] if oncall else None,
        "experts": experts, "decisions": decisions, "docs": docs,
        "procedure": proc[0] if proc else None,
        "action": {"id": action_id, "type": "rollback_proposal", "deployment": top["dep_id"],
                   "status": "pending_approval"},
    }


# ───────────────────────────── orchestration ─────────────────────────────
def recall(user: str, tenant: str, svc: str = "", incident: str = "") -> list[dict]:
    use_tenant(tenant)
    rows = q(Q.EPISODES_RECALL, {"user": user, "tenant": tenant})
    return [r for r in rows if not (svc or incident) or r["service"] == svc or r["incident"] == incident][:5]


def evidence_graph(incident: dict, top: dict | None, rem: dict, similar: list) -> dict:
    nodes, edges = {}, []

    def n(id_, label, kind):
        nodes.setdefault(id_, {"id": id_, "label": label, "kind": kind})

    def e(a, b, rel):
        edges.append({"from": a, "to": b, "label": rel})

    n(incident["id"], incident["id"], "incident")
    n(incident["service"], incident["service"], "service")
    e(incident["id"], incident["service"], "AFFECTS")
    if top:
        for a, b in zip(top["path"], top["path"][1:]):
            n(a, a, "service"); n(b, b, "service"); e(a, b, "DEPENDS_ON")
        n(top["dep_id"], f"{top['dep_id']} v{top['version']}", "deployment")
        e(top["dep_id"], top["service"], "DEPLOYED_TO")
        if top["author"]:
            n(top["author"], top["author"], "person"); e(top["author"], top["dep_id"], "AUTHORED")
    for s in similar[:2]:
        n(s["incident"], s["incident"], "history"); e(incident["id"], s["incident"], "SIMILAR")
        if s["cause_dep"]:
            n(s["cause_dep"], s["cause_dep"], "deployment"); e(s["cause_dep"], s["incident"], "CAUSED")
        if s["decision"]:
            n(s["decision"], s["decision"], "decision"); e(s["decision"], s["incident"], "RESOLVES")
        if s["doc"]:
            n(s["doc"], s["doc"], "document"); e(s["decision"], s["doc"], "RECORDED_IN")
    return {"nodes": list(nodes.values()), "edges": edges}


def investigate(incident_id: str, tenant: str = "acme", user: str = "demo-user", question: str = "") -> dict:
    trace: list = []
    use_tenant(tenant)
    inc = q(Q.INCIDENT, {"id": incident_id})
    if not inc:
        raise KeyError(incident_id)
    memory = recall(user, tenant, inc[0]["service"], incident_id)
    if memory:
        trace.append({"agent": "Memory", "tool": "EPISODES_RECALL",
                      "result": f"{len(memory)} earlier session(s) recalled for {user}"})
    triage(incident_id, tenant, trace)
    rc = root_cause(incident_id, tenant, trace)
    rem = remediation(incident_id, tenant, trace)
    top = rc["top"]
    summary = (f"Most likely cause: deployment {top['dep_id']} v{top['version']} on {top['service']} "
               f"({top['summary']}), by {top['author']}. Confidence {top['confidence']:.0%}. "
               f"Page {rem.get('page')} ({rem.get('team')}) and approve rollback."
               if top else "No candidate found.")
    if top:
        run(Q.EPISODE_CREATE, {"user": user, "tenant": tenant, "eid": _id("EP"), "ts": int(time.time() * 1000),
                               "question": question or f"Investigate {incident_id}", "summary": summary,
                               "suspect": top["dep_id"], "incident": incident_id, "suspect_service": top["service"]})
        trace.append({"agent": "Memory", "tool": "EPISODE_CREATE", "result": "Episode saved to graph memory"})
    return {"incident": rc["incident"], "summary": summary, "ranked": rc["ranked"], "similar": rc["similar"],
            "blast_radius": rc["blast"], "remediation": rem, "memory_recalled": memory, "trace": trace,
            "handoffs": q(Q.TASK_TRACE, {"incident": incident_id, "tenant": tenant}),
            "graph": evidence_graph(rc["incident"], top, rem, rc["similar"])}


def confirm(incident_id: str, dep_id: str, tenant: str, user: str) -> dict:
    """Human confirms the root cause -> graph learns (semantic memory + new decision)."""
    use_tenant(tenant)
    inc = q(Q.INCIDENT, {"id": incident_id})
    if not inc:
        raise KeyError(incident_id)
    inc = inc[0]
    dep = q("MATCH (d:Deployment {id:$d})-[:DEPLOYED_TO]->(s:Service) RETURN s.name AS svc, d.change_type AS ct, d.summary AS sm",
            {"d": dep_id})
    if not dep:
        raise KeyError(dep_id)
    dep = dep[0]
    ts = int(time.time() * 1000)
    for sy in inc["symptoms"]:
        run(Q.FACT_UPSERT, {"svc": dep["svc"], "symptom": sy, "tenant": tenant, "ctype": dep["ct"], "ts": ts,
                            "key": f"{tenant}|{dep['svc']}|{dep['ct']}|{sy}",
                            "statement": f"{dep['ct']} change on {dep['svc']} caused {sy}"})
    run(Q.CONFIRM_ROOT_CAUSE, {"dep": dep_id, "incident": incident_id, "decid": _id("DEC"), "ts": ts, "user": user,
                               "title": f"Rollback {dep_id}: {dep['sm']}", "why": "Confirmed by on-call via GraphSentinel"})
    return {"learned": len(inc["symptoms"]), "incident_status": "resolved"}


def approve(action_id: str, tenant: str, user: str) -> dict:
    use_tenant(tenant)
    a = q(Q.ACTION_GET, {"id": action_id, "tenant": tenant})
    if not a:
        raise KeyError(action_id)
    run(Q.ACTION_APPROVE, {"id": action_id, "tenant": tenant, "user": user, "ts": int(time.time() * 1000)})
    return {"id": action_id, "status": "approved", "note": f"Rollback of {a[0]['dep']} approved by {user} (simulated)"}
