"""Synthetic company knowledge graph for 'Acme Commerce' (100% fabricated data).

Run:  python -m app.seed          (idempotent: wipes and rebuilds the graph)
"""
import time

from .db import TENANTS, get_graph, run, use_tenant

H = 3600
NOW = int(time.time())


def ago(hours: float) -> int:
    return int(NOW - hours * H)


TEAMS = ["Platform", "Payments", "Identity", "Data", "SRE"]

# name, team, role, oncall
PEOPLE = [
    ("Asha Rao", "Payments", "Staff Engineer", True),
    ("Liam Chen", "Payments", "Engineer", False),
    ("Priya Nair", "Platform", "Engineering Manager", False),
    ("Omar Haddad", "Platform", "Engineer", True),
    ("Sofia Rossi", "Identity", "Security Engineer", True),
    ("Kenji Sato", "Identity", "Engineer", False),
    ("Maya Patel", "Data", "Data Engineer", True),
    ("Noah Williams", "SRE", "SRE Lead", True),
    ("Zara Ali", "SRE", "SRE", False),
]

# service, owner team, tier
SERVICES = [
    ("api-gateway", "Platform", 1),
    ("checkout-service", "Payments", 1),
    ("payments-api", "Payments", 1),
    ("fraud-service", "Payments", 2),
    ("auth-service", "Identity", 1),
    ("user-db", "Data", 1),
    ("orders-db", "Data", 1),
    ("redis-cache", "Platform", 2),
    ("inventory-service", "Platform", 2),
    ("search-service", "Platform", 3),
    ("notification-service", "Platform", 3),
]

# (a)-[:DEPENDS_ON]->(b)
DEPENDS = [
    ("api-gateway", "checkout-service"), ("api-gateway", "auth-service"), ("api-gateway", "search-service"),
    ("checkout-service", "payments-api"), ("checkout-service", "inventory-service"),
    ("checkout-service", "orders-db"), ("checkout-service", "redis-cache"),
    ("payments-api", "fraud-service"), ("payments-api", "auth-service"),
    ("fraud-service", "user-db"),
    ("auth-service", "user-db"), ("auth-service", "redis-cache"),
    ("inventory-service", "orders-db"), ("notification-service", "orders-db"),
    ("search-service", "redis-cache"),
]

SYMPTOMS = ["high-latency", "5xx-errors", "connection-timeouts", "cache-miss-spike",
            "auth-failures", "db-pool-exhausted"]

# id, service, version, hours_ago, change_type, author, summary
DEPLOYMENTS = [
    # --- history (known root causes of resolved incidents) ---
    ("D-1001", "payments-api", "2.9.0", 900, "config", "Liam Chen", "Lower DB pool size from 50 to 10"),
    ("D-1002", "auth-service", "5.1.0", 700, "config", "Kenji Sato", "Shorten session cache TTL to 5s"),
    ("D-1003", "redis-cache", "7.2.1", 500, "infra", "Omar Haddad", "Reduce maxmemory and enable eviction"),
    ("D-1004", "fraud-service", "3.0.4", 300, "config", "Asha Rao", "Reduce upstream timeout from 2s to 200ms"),
    # --- last 24h (candidates for current incidents) ---
    ("D-2001", "fraud-service", "3.2.1", 3.0, "config", "Liam Chen", "Cut scoring-model timeout 2s -> 250ms"),
    ("D-2002", "notification-service", "1.8.0", 2.5, "code", "Omar Haddad", "Add email digest batching"),
    ("D-2003", "inventory-service", "4.4.2", 9.0, "code", "Priya Nair", "Refactor stock reservation"),
    ("D-2004", "auth-service", "5.6.0", 5.0, "config", "Kenji Sato", "Shorten session cache TTL to 3s"),
    ("D-2005", "redis-cache", "7.4.0", 4.0, "infra", "Omar Haddad", "Lower maxmemory 8GB -> 2GB"),
    ("D-2006", "search-service", "2.2.0", 20.0, "code", "Omar Haddad", "Add typo tolerance"),
]

# id, title, service, status, severity, hours_ago, symptoms, cause_deployment, resolver, resolution
INCIDENTS = [
    ("INC-101", "Checkout 5xx after payments change", "payments-api", "resolved", "SEV2", 890,
     ["5xx-errors", "db-pool-exhausted"], "D-1001", "Asha Rao", "Rolled back DB pool config"),
    ("INC-102", "Login failures spike", "auth-service", "resolved", "SEV1", 690,
     ["auth-failures", "high-latency"], "D-1002", "Sofia Rossi", "Reverted cache TTL, added TTL floor"),
    ("INC-103", "Search slow, cache thrash", "redis-cache", "resolved", "SEV3", 490,
     ["cache-miss-spike", "high-latency"], "D-1003", "Noah Williams", "Restored maxmemory"),
    ("INC-104", "Payments timing out via fraud scoring", "fraud-service", "resolved", "SEV2", 290,
     ["connection-timeouts", "5xx-errors"], "D-1004", "Asha Rao", "Rolled back timeout; added canary gate"),
    # --- currently open ---
    ("INC-201", "Checkout error rate 12% and rising", "checkout-service", "open", "SEV1", 2.0,
     ["5xx-errors", "connection-timeouts", "high-latency"], None, None, None),
    ("INC-202", "Users logged out repeatedly", "api-gateway", "open", "SEV2", 3.5,
     ["auth-failures", "high-latency"], None, None, None),
    ("INC-203", "Search results slow", "search-service", "open", "SEV3", 3.0,
     ["cache-miss-spike", "high-latency"], None, None, None),
]

# id, title, text, service, kind
DOCUMENTS = [
    ("DOC-1", "Payments timeout postmortem (INC-104)", "Aggressive upstream timeouts in fraud-service cascaded to payments-api and checkout. Rule: no timeout < 1s without canary.", "fraud-service", "postmortem"),
    ("DOC-2", "Auth session cache design", "Session TTL below 30s causes login loops under load. Floor of 30s required.", "auth-service", "design"),
    ("DOC-3", "Redis capacity runbook", "maxmemory below 4GB evicts hot keys; search and auth degrade first.", "redis-cache", "runbook"),
    ("DOC-4", "Payments DB pool guidelines", "Pool size below 25 exhausts connections at peak checkout.", "payments-api", "design"),
    ("DOC-5", "Checkout architecture overview", "checkout-service depends on payments-api, inventory, orders-db and redis-cache.", "checkout-service", "design"),
]

# id, title, rationale, made_by, service, doc, resolves_incident
DECISIONS = [
    ("DEC-1", "Roll back payments DB pool change", "Pool of 10 exhausted connections; restore 50.", "Asha Rao", "payments-api", "DOC-4", "INC-101"),
    ("DEC-2", "Revert session TTL and enforce 30s floor", "Short TTL caused login loops.", "Sofia Rossi", "auth-service", "DOC-2", "INC-102"),
    ("DEC-3", "Restore Redis maxmemory", "Eviction thrash degraded search and auth.", "Noah Williams", "redis-cache", "DOC-3", "INC-103"),
    ("DEC-4", "Roll back fraud timeout; require canary for timeout changes", "Cascading timeouts across the payment path.", "Asha Rao", "fraud-service", "DOC-1", "INC-104"),
]

# id, title, steps, applies_to symptoms
PROCEDURES = [
    ("PROC-1", "Roll back the suspect deployment", "1) Identify suspect deploy 2) Roll back via pipeline 3) Watch error rate 10 min 4) Open postmortem", ["5xx-errors", "connection-timeouts"]),
    ("PROC-2", "Restore cache capacity", "1) Check eviction rate 2) Raise maxmemory 3) Warm hot keys", ["cache-miss-spike"]),
    ("PROC-3", "Stabilise auth sessions", "1) Check session TTL 2) Restore >=30s 3) Force token refresh", ["auth-failures"]),
]


def seed(tenant: str | None = None) -> dict:
    """Rebuild one tenant's graph (default tenant if None)."""
    use_tenant(tenant)
    g = get_graph()
    try:
        g.delete()
    except Exception:
        pass
    g = get_graph()
    for label, prop in [("Service", "name"), ("Incident", "id"), ("Deployment", "id"), ("Person", "name"),
                        ("Team", "name"), ("Symptom", "name"), ("Decision", "id"), ("Document", "id"),
                        ("Fact", "key"), ("Task", "id")]:
        try:
            run(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass

    for t in TEAMS:
        run("MERGE (:Team {name:$n})", {"n": t})
    for name, team, role, oncall in PEOPLE:
        run("MATCH (t:Team {name:$t}) CREATE (:Person {name:$n, role:$r, oncall:$o})-[:MEMBER_OF]->(t)",
            {"n": name, "t": team, "r": role, "o": oncall})
    for name, team, tier in SERVICES:
        run("MATCH (t:Team {name:$t}) CREATE (t)-[:OWNS]->(:Service {name:$n, tier:$tier})",
            {"n": name, "t": team, "tier": tier})
    for a, b in DEPENDS:
        run("MATCH (a:Service {name:$a}),(b:Service {name:$b}) CREATE (a)-[:DEPENDS_ON]->(b)", {"a": a, "b": b})
    for s in SYMPTOMS:
        run("CREATE (:Symptom {name:$n})", {"n": s})

    for did, svc, ver, h, ctype, author, summary in DEPLOYMENTS:
        run("""MATCH (s:Service {name:$svc}), (p:Person {name:$author})
               CREATE (p)-[:AUTHORED]->(d:Deployment {id:$id, version:$ver, ts:$ts, change_type:$ct, summary:$sum})
               CREATE (d)-[:DEPLOYED_TO]->(s)""",
            {"svc": svc, "author": author, "id": did, "ver": ver, "ts": ago(h), "ct": ctype, "sum": summary})

    for iid, title, svc, status, sev, h, syms, cause, resolver, resolution in INCIDENTS:
        run("""MATCH (s:Service {name:$svc})
               CREATE (i:Incident {id:$id, title:$title, status:$st, severity:$sev, started_at:$ts})
               CREATE (i)-[:AFFECTS]->(s)""",
            {"svc": svc, "id": iid, "title": title, "st": status, "sev": sev, "ts": ago(h)})
        for sy in syms:
            run("MATCH (i:Incident {id:$i}),(s:Symptom {name:$s}) CREATE (i)-[:HAS_SYMPTOM]->(s)", {"i": iid, "s": sy})
        if cause:
            run("MATCH (d:Deployment {id:$d}),(i:Incident {id:$i}) CREATE (d)-[:CAUSED]->(i)", {"d": cause, "i": iid})
        if resolver:
            run("MATCH (p:Person {name:$p}),(i:Incident {id:$i}) CREATE (p)-[:RESOLVED {note:$n}]->(i)",
                {"p": resolver, "i": iid, "n": resolution})

    for did, title, text, svc, kind in DOCUMENTS:
        run("MATCH (s:Service {name:$s}) CREATE (:Document {id:$id, title:$t, text:$x, kind:$k})-[:DOCUMENTS]->(s)",
            {"s": svc, "id": did, "t": title, "x": text, "k": kind})
    for did, title, why, who, svc, doc, inc in DECISIONS:
        run("""MATCH (p:Person {name:$who}),(s:Service {name:$svc}),(d:Document {id:$doc}),(i:Incident {id:$inc})
               CREATE (dec:Decision {id:$id, title:$t, rationale:$why, ts:i.started_at + 7200})
               CREATE (dec)-[:MADE_BY]->(p), (dec)-[:ABOUT]->(s), (dec)-[:RECORDED_IN]->(d), (dec)-[:RESOLVES]->(i)""",
            {"who": who, "svc": svc, "doc": doc, "inc": inc, "id": did, "t": title, "why": why})
    for pid, title, steps, syms in PROCEDURES:
        run("CREATE (:Procedure {id:$id, title:$t, steps:$s})", {"id": pid, "t": title, "s": steps})
        for sy in syms:
            run("MATCH (p:Procedure {id:$id}),(s:Symptom {name:$s}) CREATE (p)-[:APPLIES_TO]->(s)", {"id": pid, "s": sy})

    for a in ["TriageAgent", "RootCauseAgent", "RemediationAgent"]:
        run("MERGE (:Agent {name:$n})", {"n": a})

    c = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    e = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return {"nodes": c, "edges": e}


def seed_all() -> dict:
    return {t: seed(t) for t in TENANTS}


if __name__ == "__main__":
    print(seed_all())
