import os
import tempfile

os.environ["FALKORDB_LITE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["GRAPH_NAME"] = "gs_test"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        client.post("/api/seed")
        yield client


def test_health(c):
    h = c.get("/health").json()
    assert h["nodes"] > 50 and h["edges"] > 100


def test_root_cause_multi_hop(c):
    r = c.post("/api/incidents/INC-201/investigate", json={"question": "", "user": "u1"}).json()
    top = r["ranked"][0]
    assert top["dep_id"] == "D-2001" and top["hops"] == 2           # fraud-service, 2 hops from checkout
    assert top["path"] == ["checkout-service", "payments-api", "fraud-service"]
    assert [h["to_agent"] for h in r["handoffs"]][:2] == ["RootCauseAgent", "RemediationAgent"]
    assert r["remediation"]["action"]["status"] == "pending_approval"


def test_other_incidents(c):
    assert c.post("/api/incidents/INC-202/investigate").json()["ranked"][0]["service"] == "auth-service"
    assert c.post("/api/incidents/INC-203/investigate").json()["ranked"][0]["service"] == "redis-cache"


def test_memory_and_learning(c):
    before = c.post("/api/incidents/INC-203/investigate", json={"question": "", "user": "u2"}).json()
    assert c.post("/api/incidents/INC-203/confirm", json={"deployment_id": "D-2005", "user": "u2"}).status_code == 200
    mem = c.get("/api/memory", params={"user": "u2"}).json()
    assert mem["facts"] and mem["episodes"]
    # semantic memory now boosts the same pattern on a different incident
    after = c.post("/api/incidents/INC-202/investigate", json={"question": "", "user": "u2"}).json()
    redis = next(r for r in after["ranked"] if r["service"] == "redis-cache")
    assert redis["breakdown"]["memory"] > 0
    assert before["ranked"][0]["breakdown"]["memory"] == 0


def test_tenant_isolation(c):
    # acme learned facts in test_memory_and_learning; globex has its own graph and must see none of it
    assert c.get("/api/memory", params={"tenant": "acme", "user": "u2"}).json()["facts"]
    assert c.get("/api/memory", params={"tenant": "globex", "user": "u2"}).json() == {"episodes": [], "facts": []}
    # learning in acme must not change globex ranking
    c.post("/api/incidents/INC-203/investigate", json={"question": "", "tenant": "globex", "user": "u2"})
    after = c.post("/api/incidents/INC-202/investigate", json={"question": "", "tenant": "globex", "user": "u2"}).json()
    assert all(r["breakdown"]["memory"] == 0 for r in after["ranked"])


def test_unknown_tenant_rejected(c):
    assert c.get("/api/memory", params={"tenant": "evil", "user": "u"}).status_code == 404


def test_shortest_path_tool(c):
    r = c.post("/api/incidents/INC-201/investigate", json={"question": "", "tenant": "globex", "user": "u3"}).json()
    sp = [t for t in r["trace"] if t["tool"].startswith("shortestPath")]
    assert sp and "checkout-service -> payments-api -> fraud-service" in sp[0]["result"]


def test_company_brain(c):
    assert "Payments" in c.post("/api/ask", json={"question": "who owns payments-api?"}).json()["answer"]
    assert "DOC-1" in c.post("/api/ask", json={"question": "why decisions on fraud-service"}).json()["answer"]
