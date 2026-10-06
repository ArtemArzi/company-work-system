#!/usr/bin/env python3
"""Thin shared CLI: preparation, fixed operations, validation and delivery."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
# Keep doctor available before third-party dependencies exist.
if __name__ == "__main__" and (sys.argv[1:] == ["doctor"] or len(sys.argv) == 4 and sys.argv[1] == "--root" and sys.argv[3] == "doctor"):
    from doctor import diagnose
    target = Path(sys.argv[2]) if len(sys.argv) == 4 else Path(__file__).resolve().parents[1]
    report = diagnose(target)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    raise SystemExit(2 if report["blockers"] else 0)
from core import load, require
import delivery
import operations
import validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("validate")
    sub.add_parser("self-test")
    p = sub.add_parser("preflight"); p.add_argument("--remote"); p.add_argument("--branch", default="main")
    p = sub.add_parser("context"); p.add_argument("--task")
    p = sub.add_parser("intake"); p.add_argument("id"); p.add_argument("--request", required=True); p.add_argument("--owner", required=True); p.add_argument("--acceptance", type=Path, required=True); p.add_argument("--confirmed", action="store_true"); p.add_argument("--unknown", action="append", default=[]); p.add_argument("--input", action="append", default=[]); p.add_argument("--independent-required", action="store_true"); p.add_argument("--skill"); p.add_argument("--harness", choices=['codex', 'claude']); p.add_argument("--source-id", action='append', default=[])
    p = sub.add_parser("decide"); p.add_argument("id"); p.add_argument("action", choices=["confirm", "cancel", "resume"]); p.add_argument("--revision", type=int, required=True); p.add_argument("--reason", required=True); p.add_argument("--actor", required=True)
    p = sub.add_parser("execute"); p.add_argument("id"); p.add_argument("--artifact", type=Path, required=True); p.add_argument("--critical", type=Path, required=True); p.add_argument("--review", type=Path)
    p = sub.add_parser("observe"); p.add_argument("id"); p.add_argument("layer", choices=["application", "effect"]); p.add_argument("--evidence", required=True); p.add_argument("--actor", required=True)
    p = sub.add_parser("summary"); p.add_argument("--task", help="Owning task for immutable evidence snapshot")
    p = sub.add_parser("source-read"); p.add_argument("id")
    p = sub.add_parser("reconcile"); p.add_argument("left", type=Path); p.add_argument("right", type=Path)
    p = sub.add_parser("episodes"); p.add_argument("source", type=Path); p.add_argument("--denominator", type=int, required=True)
    sub.add_parser("tick")
    p = sub.add_parser("incident"); p.add_argument("id"); p.add_argument("--observed", required=True); p.add_argument("--assumption", required=True); p.add_argument("--proposal", required=True)
    p = sub.add_parser("search"); p.add_argument("query")
    p = sub.add_parser("kb-read"); p.add_argument("id")
    p = sub.add_parser("create"); p.add_argument("release", type=Path); p.add_argument("destination", type=Path); p.add_argument("--id", required=True); p.add_argument("--owner", required=True)
    p = sub.add_parser("prepare"); p.add_argument("remote"); p.add_argument("destination", type=Path); p.add_argument("--branch", default="main")
    p = sub.add_parser("commit"); p.add_argument("paths", nargs="+"); p.add_argument("--message", required=True)
    p = sub.add_parser("deliver"); p.add_argument("remote"); p.add_argument("--branch", default="main"); p.add_argument("--expected-base"); p.add_argument("--task")
    p = sub.add_parser("update"); p.add_argument("release", type=Path); p.add_argument("destination", type=Path)
    p = sub.add_parser("finish-update"); p.add_argument("--target", required=True); p.add_argument("--company-base", required=True)
    p = sub.add_parser("rollback"); p.add_argument("destination", type=Path)
    p = sub.add_parser("backup"); p.add_argument("destination", type=Path)
    p = sub.add_parser("restore"); p.add_argument("backup", type=Path); p.add_argument("destination", type=Path)
    p = sub.add_parser("proposal"); p.add_argument("package", type=Path); p.add_argument("destination", type=Path)
    p = sub.add_parser("proposal-candidate"); p.add_argument("package", type=Path); p.add_argument("product", type=Path); p.add_argument("remote"); p.add_argument("destination", type=Path)
    p = sub.add_parser('hooks-check'); p.add_argument('event', choices=['SessionStart','PreToolUse','PostToolUse','PreCompact','Stop','context-entry','entity-before-write','entity-after-write','method-impact','task-continuation','publication-check']); p.add_argument('--payload', type=Path); p.add_argument('--task')
    p = sub.add_parser('hooks-project'); p.add_argument('harness', choices=['codex','claude']); p.add_argument('--apply', action='store_true'); p.add_argument('--enabled', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    c = args.command
    if c == "doctor":
        from doctor import diagnose
        return diagnose(root)
    if c == 'source-read':
        import connectors
    if c in {'search', 'kb-read'}:
        import knowledge
    if c in {'create', 'update', 'finish-update', 'rollback', 'backup', 'restore', 'proposal', 'proposal-candidate'}:
        import lifecycle
    if c == "validate": result = validation.repository(root)
    elif c == "self-test": result = validation.self_test()
    elif c == "preflight": result = delivery.preflight(root, args.remote, args.branch)
    elif c == "context": result = operations.context(root, args.task)
    elif c == "intake": result = operations.intake(root, args.id, args.request, args.owner, load(args.acceptance), args.confirmed, args.unknown, args.input, args.independent_required, args.skill, args.harness, args.source_id)
    elif c == "decide": result = operations.decide(root, args.id, args.revision, args.action, args.reason, args.actor)
    elif c == "execute": result = operations.execute(root, args.id, load(args.artifact), load(args.critical), load(args.review) if args.review else None)
    elif c == "observe": result = operations.observe(root, args.id, args.layer, args.evidence, args.actor)
    elif c == "summary": result = operations.summary(root, args.task)
    elif c == "source-read": result = connectors.read(root, args.id)
    elif c == "reconcile": result = operations.reconcile(load(args.left), load(args.right))
    elif c == "episodes": result = operations.episodes(load(args.source), args.denominator)
    elif c == "tick": result = operations.tick(root)
    elif c == "incident": result = operations.incident(root, args.id, args.observed, args.assumption, args.proposal)
    elif c == "search": result = knowledge.search(root, args.query)
    elif c == "kb-read": result = knowledge.read_kb(root, args.id)
    elif c == "create": result = lifecycle.create(args.release, args.destination, args.id, args.owner)
    elif c == "prepare": result = delivery.prepare(root, args.remote, args.branch, args.destination)
    elif c == "commit": result = {"sha256": delivery.commit(root, args.paths, args.message)}
    elif c == "deliver": result = delivery.deliver(root, args.remote, args.branch, args.expected_base, args.task)
    elif c == "update": result = lifecycle.update(root, args.release, args.destination)
    elif c == "finish-update": result = lifecycle.finish_update(root, args.target, args.company_base)
    elif c == "rollback": result = lifecycle.rollback(root, args.destination)
    elif c == "backup": result = lifecycle.backup(root, args.destination)
    elif c == "restore": result = lifecycle.restore(args.backup, args.destination)
    elif c == "proposal": result = lifecycle.proposal(root, args.package, args.destination)
    elif c == "proposal-candidate": result = lifecycle.proposal_candidate(root, args.package, args.product, args.remote, args.destination)
    elif c == 'hooks-check':
        import hooks
        result = hooks.dispatch(root, 'common', args.event, load(args.payload) if args.payload else {}, task_id=args.task)
    elif c == 'hooks-project':
        import hooks
        result = hooks.project(root, args.harness, apply=args.apply, enabled=args.enabled)
    else: raise ValueError("unsupported operation")
    if c == "preflight" and result["status"] == "blocked":
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
