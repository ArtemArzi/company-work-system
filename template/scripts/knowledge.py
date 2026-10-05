"""Derived graph/search: always points to the current canonical source."""
from pathlib import Path
from core import config, digest, load, object_hash, path, require, tasks, write


def build(root):
    nodes, edges = [], []
    for file in sorted(Path(root, "company/sources").glob("*/source.yaml")):
        meta = load(file)
        relative = str(file.relative_to(root))
        node = {"id": meta["id"], "aliases": meta.get("aliases", []), "path": relative, "sha256": digest(file), "origin": meta["origin"], "status": meta["status"], "text": meta.get("summary", "")}
        if meta.get("material"):
            material = path(root, meta["material"])
            require(material.is_file(), "knowledge material missing")
            node.update(material=meta["material"], material_sha256=digest(material), text=material.read_text())
        nodes.append(node)
        for edge in meta.get("links", []):
            require(edge.get("type") in {"uses", "supports", "supersedes", "related"} and edge.get("level") in {"observation", "hypothesis"}, "unproven/unknown relation")
            edges.append({"from": node["id"], **edge, "source": relative, "source_sha256": node["sha256"]})
    ids = {node["id"] for node in nodes}
    require(all(e["to"] in ids for e in edges), "graph missing target")
    result = {"schema_version": 1, "nodes": nodes, "edges": edges}
    result["fingerprint"] = object_hash(result)
    return result


def search(root, query):
    require(query.strip(), "empty search")
    cfg = config(root)
    current = build(root)
    if cfg["features"]["graph"]:
        cache = path(root, ".system/cache/graph.json")
        if not cache.exists() or load(cache).get("fingerprint") != current["fingerprint"]:
            write(cache, current)
    hits = []
    for node in current["nodes"]:
        haystack = " ".join([node["id"], *node["aliases"], node["text"]]).lower()
        if query.lower() in haystack:
            hits.append({**node, "excerpt": node["text"][:1200], "links": [e for e in current["edges"] if e["from"] == node["id"]]})
    return {"mode": "derived-graph" if cfg["features"]["graph"] else "direct-source", "hits": hits, "limitations": ["Relations retain observation/hypothesis; causality is not inferred"]}


def read_kb(root, source_id):
    require(config(root)["features"]["knowledge_base"], "knowledge-base adapter disabled; use direct-source search")
    from connectors import read
    return read(root, source_id)
