"""Every Cypher query / graph algorithm GraphSentinel depends on, in one place.

If FalkorDB is removed the agent cannot: find candidate root causes (multi-hop
DEPENDS_ON traversal), find similar past incidents (shared Symptom nodes), trace
decisions to documents, recall memory, or hand work between agents.
"""

# ───────────────────────── Track 1: act on connected data ─────────────────────────
INCIDENT = """
MATCH (i:Incident {id:$id})-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (i)-[:HAS_SYMPTOM]->(sy:Symptom)
RETURN i.id AS id, i.title AS title, i.status AS status, i.severity AS severity,
       i.started_at AS started_at, s.name AS service, collect(sy.name) AS symptoms
"""

OPEN_INCIDENTS = """
MATCH (i:Incident {status:'open'})-[:AFFECTS]->(s:Service)
RETURN i.id AS id, i.title AS title, i.severity AS severity, s.name AS service
ORDER BY i.severity, i.started_at DESC
"""

# Multi-hop blast radius: every service that (transitively) depends on the affected one.
BLAST_RADIUS = """
MATCH p=(d:Service)-[:DEPENDS_ON*1..3]->(a:Service {name:$svc})
OPTIONAL MATCH (t:Team)-[:OWNS]->(d)
RETURN d.name AS service, d.tier AS tier, min(length(p)) AS hops, t.name AS team
ORDER BY tier, hops
"""

# Graph algorithm: PageRank over DEPENDS_ON = how critical a service is to the estate.
PAGERANK = """
CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score
RETURN node.name AS service, score ORDER BY score DESC
"""

# Graph algorithm: shortest dependency path (BFS) from the affected service to the suspect service.
SHORTEST_PATH = """
MATCH (a:Service {name:$src}), (b:Service {name:$dst})
WITH shortestPath((a)-[:DEPENDS_ON*..6]->(b)) AS p
RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops
"""

# Candidate root causes: recent deployments on the affected service or anything it
# depends on (0-3 hops downstream), keeping the actual dependency path as evidence.
CANDIDATES = """
MATCH (i:Incident {id:$id})-[:AFFECTS]->(a:Service)
MATCH p=(a)-[:DEPENDS_ON*0..3]->(c:Service)
MATCH (d:Deployment)-[:DEPLOYED_TO]->(c)
WHERE d.ts <= i.started_at AND d.ts >= i.started_at - $window
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(d)
RETURN c.name AS service, length(p) AS hops, [n IN nodes(p) | n.name] AS path,
       d.id AS dep_id, d.version AS version, d.ts AS ts, d.change_type AS change_type,
       d.summary AS summary, au.name AS author, i.started_at AS incident_ts
"""

# Similar past incidents = shared Symptom nodes; return known cause, decision and source doc.
SIMILAR = """
MATCH (i:Incident {id:$id})-[:HAS_SYMPTOM]->(s:Symptom)<-[:HAS_SYMPTOM]-(o:Incident {status:'resolved'})
WITH i, o, collect(DISTINCT s.name) AS shared
MATCH (i)-[:HAS_SYMPTOM]->(all:Symptom)
WITH o, shared, count(DISTINCT all) AS total
OPTIONAL MATCH (cd:Deployment)-[:CAUSED]->(o)
OPTIONAL MATCH (cd)-[:DEPLOYED_TO]->(cs:Service)
OPTIONAL MATCH (dec:Decision)-[:RESOLVES]->(o)
OPTIONAL MATCH (dec)-[:RECORDED_IN]->(doc:Document)
OPTIONAL MATCH (who:Person)-[:RESOLVED]->(o)
RETURN o.id AS incident, o.title AS title, shared, toFloat(size(shared))/total AS similarity,
       cd.id AS cause_dep, cs.name AS cause_service, cd.change_type AS cause_change_type,
       dec.id AS decision, dec.title AS decision_title, doc.id AS doc, doc.title AS doc_title,
       who.name AS resolver
ORDER BY similarity DESC
"""

# ───────────────────────── Track 3: company brain ─────────────────────────
OWNERS = """
MATCH (t:Team)-[:OWNS]->(s:Service {name:$svc})
OPTIONAL MATCH (p:Person)-[:MEMBER_OF]->(t)
RETURN t.name AS team, p.name AS person, p.role AS role, p.oncall AS oncall
ORDER BY oncall DESC
"""

# "Who knows what": people who resolved incidents on the service or its dependencies.
EXPERTS = """
MATCH (s:Service {name:$svc})-[:DEPENDS_ON*0..2]->(c:Service)<-[:AFFECTS]-(i:Incident)<-[:RESOLVED]-(p:Person)
RETURN p.name AS person, count(DISTINCT i) AS incidents, collect(DISTINCT i.id) AS which
ORDER BY incidents DESC LIMIT 3
"""

# Trace decisions back to their source documents and authors.
DECISIONS_FOR_SERVICE = """
MATCH (dec:Decision)-[:ABOUT]->(s:Service {name:$svc})
OPTIONAL MATCH (dec)-[:MADE_BY]->(p:Person)
OPTIONAL MATCH (dec)-[:RECORDED_IN]->(doc:Document)
OPTIONAL MATCH (dec)-[:RESOLVES]->(i:Incident)
RETURN dec.id AS id, dec.title AS title, dec.rationale AS rationale, p.name AS made_by,
       doc.id AS doc, doc.title AS doc_title, doc.text AS doc_text, i.id AS incident
"""

DOCS_FOR_SERVICES = """
MATCH (d:Document)-[:DOCUMENTS]->(s:Service) WHERE s.name IN $svcs
RETURN d.id AS id, d.title AS title, d.text AS text, s.name AS service
"""

PROCEDURE_FOR_SYMPTOMS = """
MATCH (p:Procedure)-[:APPLIES_TO]->(s:Symptom) WHERE s.name IN $symptoms
RETURN p.id AS id, p.title AS title, p.steps AS steps, collect(s.name) AS symptoms
ORDER BY size(symptoms) DESC LIMIT 1
"""

SERVICE_NAMES = "MATCH (s:Service) RETURN s.name AS name"

OVERVIEW_NODES = "MATCH (s:Service) OPTIONAL MATCH (t:Team)-[:OWNS]->(s) RETURN s.name AS name, s.tier AS tier, t.name AS team"
OVERVIEW_EDGES = "MATCH (a:Service)-[:DEPENDS_ON]->(b:Service) RETURN a.name AS a, b.name AS b"

# ───────────────────────── Track 2: memory & coordination ─────────────────────────
# Episodic memory (per tenant + user)
EPISODE_CREATE = """
MERGE (u:User {id:$user, tenant:$tenant})
CREATE (e:Episode {id:$eid, tenant:$tenant, ts:$ts, question:$question, summary:$summary, suspect:$suspect})
CREATE (u)-[:HAD]->(e)
WITH e
MATCH (i:Incident {id:$incident}) CREATE (e)-[:CONCERNS]->(i)
WITH e
MATCH (s:Service {name:$suspect_service}) CREATE (e)-[:CONCLUDED_ON]->(s)
"""

EPISODES_RECALL = """
MATCH (u:User {id:$user, tenant:$tenant})-[:HAD]->(e:Episode)
OPTIONAL MATCH (e)-[:CONCERNS]->(i:Incident)
OPTIONAL MATCH (e)-[:CONCLUDED_ON]->(s:Service)
RETURN e.id AS id, e.ts AS ts, e.summary AS summary, i.id AS incident, s.name AS service
ORDER BY e.ts DESC LIMIT 25
"""

# Semantic memory: learned facts, strengthened by human confirmation.
FACT_UPSERT = """
MATCH (s:Service {name:$svc}), (sy:Symptom {name:$symptom})
MERGE (f:Fact {key:$key})
ON CREATE SET f.tenant=$tenant, f.change_type=$ctype, f.confirmations=0, f.statement=$statement
SET f.confirmations = f.confirmations + 1, f.updated_at=$ts
MERGE (f)-[:ABOUT]->(s)
MERGE (f)-[:ABOUT]->(sy)
"""

FACTS_FOR_CANDIDATES = """
MATCH (f:Fact {tenant:$tenant})-[:ABOUT]->(s:Service), (f)-[:ABOUT]->(sy:Symptom)
WHERE s.name IN $svcs AND sy.name IN $symptoms
RETURN s.name AS service, sum(f.confirmations) AS confirmations, collect(f.statement) AS statements
"""

# Shared state / handoffs between agents (persisted in the graph, not in process memory)
TASK_HANDOFF = """
MATCH (from:Agent {name:$from}), (to:Agent {name:$to})
CREATE (t:Task {id:$id, tenant:$tenant, incident:$incident, ts:$ts, payload:$payload, status:'pending'})
CREATE (from)-[:PRODUCED]->(t)-[:HANDED_TO]->(to)
"""

TASK_READ = """
MATCH (:Agent)-[:PRODUCED]->(t:Task {incident:$incident, tenant:$tenant})-[:HANDED_TO]->(:Agent {name:$to})
RETURN t.payload AS payload ORDER BY t.ts DESC LIMIT 1
"""

TASK_TRACE = """
MATCH (a:Agent)-[:PRODUCED]->(t:Task {incident:$incident, tenant:$tenant})-[:HANDED_TO]->(b:Agent)
RETURN a.name AS from_agent, b.name AS to_agent, t.ts AS ts ORDER BY t.ts
"""

# Agent actions (human-in-the-loop)
ACTION_CREATE = """
MATCH (d:Deployment {id:$dep}), (a:Agent {name:'RemediationAgent'})
CREATE (x:Action {id:$id, tenant:$tenant, type:'rollback_proposal', status:'pending_approval', ts:$ts, incident:$incident})
CREATE (a)-[:PROPOSED]->(x)-[:TARGETS]->(d)
"""
ACTION_GET = "MATCH (x:Action {id:$id, tenant:$tenant})-[:TARGETS]->(d:Deployment) RETURN x.id AS id, x.status AS status, x.incident AS incident, d.id AS dep"
ACTION_APPROVE = "MATCH (x:Action {id:$id, tenant:$tenant}) SET x.status='approved', x.approved_by=$user, x.approved_at=$ts"

# Learning loop: confirmed root cause becomes company knowledge.
CONFIRM_ROOT_CAUSE = """
MATCH (d:Deployment {id:$dep}), (i:Incident {id:$incident})
MERGE (d)-[:CAUSED]->(i)
SET i.status='resolved'
WITH d, i
CREATE (dec:Decision {id:$decid, title:$title, rationale:$why, ts:$ts, made_by:$user})
CREATE (dec)-[:RESOLVES]->(i)
WITH d, i, dec
MATCH (d)-[:DEPLOYED_TO]->(s:Service)
CREATE (dec)-[:ABOUT]->(s)
"""
