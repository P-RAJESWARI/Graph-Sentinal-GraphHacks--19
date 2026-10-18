"""FalkorDB connection. Works with Docker, FalkorDB Cloud, or embedded falkordblite (dev/tests).

Tenant isolation: every tenant gets its OWN graph (`<GRAPH_NAME>_<tenant>`) in the same FalkorDB
instance (FalkorDB multigraph), so one tenant's data and agent memory can never be matched by another tenant's Cypher.
"""
import os
import re
from contextvars import ContextVar
from functools import lru_cache

GRAPH_NAME = os.getenv("GRAPH_NAME", "graphsentinel")
DEFAULT_TENANT = "acme"
# Tenants provisioned at startup. Unknown tenants are rejected (no unbounded graph creation).
TENANTS = [t.strip() for t in os.getenv("TENANTS", "acme,globex").split(",") if t.strip()]

_tenant: ContextVar[str] = ContextVar("tenant", default=DEFAULT_TENANT)


class UnknownTenant(KeyError):
    pass


def use_tenant(tenant: str | None) -> str:
    """Route all following q()/run() calls in this request to the tenant's graph."""
    tenant = tenant or DEFAULT_TENANT
    if tenant not in TENANTS or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tenant):
        raise UnknownTenant(tenant)
    _tenant.set(tenant)
    return tenant


@lru_cache(maxsize=1)
def _client():
    lite = os.getenv("FALKORDB_LITE_PATH")
    if lite:
        from redislite.falkordb_client import FalkorDB
        return FalkorDB(lite)
    from falkordb import FalkorDB
    return FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6379")),
        username=os.getenv("FALKORDB_USERNAME") or None,
        password=os.getenv("FALKORDB_PASSWORD") or None,
    )


@lru_cache(maxsize=64)
def _graph(name: str):
    return _client().select_graph(name)


def get_graph(tenant: str | None = None):
    return _graph(f"{GRAPH_NAME}_{tenant or _tenant.get()}")


def q(cypher: str, params: dict | None = None) -> list[dict]:
    """Run Cypher and return rows as dicts keyed by the RETURN aliases."""
    res = get_graph().query(cypher, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(cols, row)) for row in res.result_set]


def run(cypher: str, params: dict | None = None) -> None:
    get_graph().query(cypher, params or {})
