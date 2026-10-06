"""Behavioral acceptance on clean synthetic companies, never live client data."""
from __future__ import annotations
import copy
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

PRODUCT = Path(__file__).resolve().parents[1]
TEMPLATE = PRODUCT / "template"
sys.path.insert(0, str(TEMPLATE / "scripts"))
from core import Rejected, config, digest, load, lock, now, object_hash, task_file, write
import connectors
import delivery
import knowledge
import lifecycle
import operations
import validation
sys.path.insert(0, str(PRODUCT / "scripts"))
import product


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="company-acceptance-")
        self.base = Path(self.tmp.name)
        self.root = self.base / "company"
        shutil.copytree(TEMPLATE, self.root, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        cfg = config(self.root)
        cfg.update(id="test-company", owner="test-owner", name="Synthetic company")
        cfg["permissions"]["local_work"] = True
        write(self.root / "company/config.yaml", cfg)
        self.addCleanup(self.tmp.cleanup)

    def seed(self, root=None):
        root = root or self.root
        delivery.git(root, "init", "-b", "main")
        delivery.identity(root)
        delivery.git(root, "add", ".")
        delivery.git(root, "commit", "-m", "Synthetic base")

    def source(self, records=None):
        return {"schema_version": 1, "company_id": "test-company", "account": "synthetic-export", "scope": "synthetic-scope", "period": "2026-10-01/2026-10-05", "unit": "test-units", "observed_at": now(), "complete": True, "records": records if records is not None else [{"id": "one", "value": 2}, {"id": "two", "value": 3}], "next_cursor": None}

    def input(self):
        write(self.root / "company/sources/input.json", self.source())
        return "company/sources/input.json"

    def task(self, task_id="test-task", independent=False, confirmed=True):
        relative = self.input()
        if "test-data" not in config(self.root).get("sources", {}):
            self.export_config()
        acceptance = {"kind": "metrics", "expected_count": 2, "expected_total": 5, "period": "2026-10-01/2026-10-05", "unit": "test-units"}
        return operations.intake(self.root, task_id, "Сверить две исходные записи и подготовить итог 5 test-units", "test-owner", acceptance, confirmed, inputs=[relative], independent_required=independent)

    def artifact(self):
        relative = "company/sources/input.json"
        source = load(self.root / relative)
        return {"schema_version": 1, "company_id": "test-company", "kind": "metrics", "summary": "2 records, total 5 test-units", "next_action": "Recipient can inspect source-backed rows", "limitations": ["Synthetic export; no provider/business effect proven"], "sources": [{"path": relative, "sha256": digest(self.root / relative)}], "complete": True, "period": source["period"], "unit": source["unit"], "records": source["records"], "total": 5}

    def critical(self):
        return {"request_alignment": "Request explicitly requires two source rows and total 5; both retained", "counterexample": "Missing value or partial row could make arithmetic look correct; rejected separately", "limitations": ["Synthetic local result; application unknown"], "references": ["company/sources/input.json"]}

    def export_config(self, kind="export", **extras):
        cfg = config(self.root)
        cfg["sources"]["test-data"] = {"type": kind, "path": "company/sources/input.json", "approved_by": "test-owner", "account": "synthetic-export", "scope": "synthetic-scope", "period": "2026-10-01/2026-10-05", "unit": "test-units", "max_age_seconds": 3600, "max_pages": 3, "timeout_seconds": 1, **extras}
        write(self.root / "company/config.yaml", cfg)

    def cli(self, *args, root=None, success=True):
        root = root or self.root
        value = subprocess.run([sys.executable, str(root / "scripts/system.py"), "--root", str(root), *args], text=True, capture_output=True, timeout=30)
        self.assertEqual(value.returncode, 0 if success else 2, value.stderr)
        return json.loads(value.stdout if success else value.stderr)


class LocalContracts(Fixture):
    def test_clean_template_and_all_pairs(self):
        self.assertEqual(validation.repository(self.root)["skills"], 12)
        self.assertFalse((self.root / "CLAUDE.md").exists())
        for harness, directory in [("codex", ".agents"), ("claude", ".claude")]:
            profile = load(self.root / f"adapters/{harness}/profile.yaml")
            self.assertEqual(len(profile["capabilities"]), 14)
            for canonical in (self.root / "skills").glob("*/SKILL.md"):
                import platform_runtime
                representation = platform_runtime.projection(self.root, directory + "/skills", canonical.parent.name)
                if representation == "symlink":
                    self.assertEqual((self.root / directory / "skills" / canonical.parent.name / "SKILL.md").read_bytes(), canonical.read_bytes())
                else:
                    self.assertEqual((self.root / directory / "skills" / canonical.parent.name).read_bytes(), ("../../skills/" + canonical.parent.name).encode())

    def test_wrong_arithmetic_then_original_case_repairs(self):
        original = self.task()
        bad = self.artifact(); bad["total"] = 6
        with self.assertRaisesRegex(Rejected, "incorrect total"):
            operations.execute(self.root, "test-task", bad, self.critical())
        failed = load(task_file(self.root, "test-task"))
        self.assertEqual(failed["errors"][0]["candidate_hash"], object_hash(bad))
        good = operations.execute(self.root, "test-task", self.artifact(), self.critical())
        self.assertEqual(good["acceptance"], original["acceptance"])
        self.assertEqual(good["status"], "verified")
        self.assertEqual(good["application"]["status"], "unknown")
        self.assertEqual(operations.execute(self.root, "test-task", self.artifact(), self.critical())["revision"], good["revision"])

    def test_result_rejects_known_bad_provenance_count_null_and_pass(self):
        t = self.task()
        for field, value in [("complete", False), ("total", 4), ("company_id", "another-company"), ("period", "different")]:
            candidate = self.artifact(); candidate[field] = value
            with self.subTest(field=field), self.assertRaises(Rejected):
                validation.result(self.root, candidate, t["acceptance"])
        for records in [[{"id": "one", "value": None}, {"id": "two", "value": 5}], [{"id": "one", "value": 5}], [{"id": "one", "value": 2}, {"id": "one", "value": 3}], [{"id": "one", "value": 1}, {"id": "two", "value": 4}]]:
            candidate = self.artifact(); candidate["records"] = records
            with self.assertRaises(Rejected): validation.result(self.root, candidate, t["acceptance"])
        with self.assertRaises(Rejected): operations.execute(self.root, "test-task", self.artifact(), {"PASS": True})

    def test_validator_itself_rejects_wrong_place_duplicate_link_cycle(self):
        file = self.root / "work/misplaced/SKILL.md"; file.parent.mkdir(parents=True); file.write_text((self.root / "skills/company-context/SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "outside canonical"): validation.repository(self.root)
        file.unlink()
        source = load(self.root / "company/sources/design-basis/source.yaml")
        write(self.root / "company/sources/duplicate/source.yaml", source)
        with self.assertRaisesRegex(Rejected, "duplicate entity"): validation.repository(self.root)
        (self.root / "company/sources/duplicate/source.yaml").unlink()
        file = self.root / "company/projects/README.md"; old = file.read_text(encoding="utf-8"); file.write_text(old + "\n[Broken](missing.md)\n", encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "broken"): validation.repository(self.root)
        file.write_text(old, encoding="utf-8")
        file = self.root / "workflows/message-to-action.yaml"; value = load(file); value["steps"][0]["depends_on"] = ["share-result"]; write(file, value)
        with self.assertRaisesRegex(Rejected, "cycle"): validation.repository(self.root)

    def test_unconfirmed_stale_revision_cancel_and_unknowns(self):
        t = self.task(confirmed=False)
        self.assertEqual(t["status"], "waiting")
        with self.assertRaises(Rejected): operations.execute(self.root, "test-task", self.artifact(), self.critical())
        with self.assertRaisesRegex(Rejected, "stale"): operations.decide(self.root, "test-task", 0, "confirm", "Confirmed exact request", "test-owner")
        t = operations.decide(self.root, "test-task", t["revision"], "cancel", "Request withdrawn", "test-owner")
        with self.assertRaises(Rejected): operations.decide(self.root, "test-task", t["revision"], "confirm", "Reopen", "test-owner")

    def test_new_process_reads_file_memory_and_blocks_changed_method(self):
        t = self.task()
        context = self.cli("context", "--task", "test-task")
        self.assertEqual(context["native_memory"], "not-used")
        self.assertEqual(context["tasks"][0]["revision"], t["revision"])
        method = self.root / "standards/task-validation.md"; method.write_text(method.read_text(encoding="utf-8") + "\nChanged method\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Pinned"): operations.execute(self.root, "test-task", self.artifact(), self.critical())
        t = load(task_file(self.root, "test-task"))
        operations.decide(self.root, "test-task", t["revision"], "resume", "Reviewed exact method change; same acceptance", "test-owner")
        self.assertEqual(operations.execute(self.root, "test-task", self.artifact(), self.critical())["status"], "verified")

    def test_independence_and_observation_are_separate(self):
        self.task(independent=True)
        review = {"reviewer": "test-owner", "verdict": "pass", "artifact_hash": object_hash(self.artifact())}
        with self.assertRaises(Rejected): operations.execute(self.root, "test-task", self.artifact(), self.critical(), review)
        review["reviewer"] = "independent-fixture-reviewer"
        t = operations.execute(self.root, "test-task", self.artifact(), self.critical(), review)
        self.assertEqual(t["effect"]["status"], "unknown")
        with self.assertRaises(Rejected): operations.observe(self.root, "test-task", "application", "fictional user", "non-owner")

    def test_lock_prevents_parallel_mutation_without_stale_lock_repair(self):
        with lock(self.root):
            rejected = self.cli("intake", "parallel-task", "--request", "Synthetic input", "--owner", "test-owner", "--acceptance", str(self.root / "release.yaml"), success=False)
            self.assertIn("already running", rejected["reason"])
        self.assertEqual(self.cli("context")["tasks"], [])

    def test_observations_bound_to_result_and_reset_with_history(self):
        self.task()
        first = operations.execute(self.root, "test-task", self.artifact(), self.critical())
        for layer in ["application", "effect"]:
            operations.observe(self.root, "test-task", layer, "Owner observed this exact local result", "test-owner")
        observed = load(task_file(self.root, "test-task"))
        self.assertEqual(observed["effect"]["result_sha256"], first["output"]["sha256"])
        self.assertEqual(operations.execute(self.root, "test-task", self.artifact(), self.critical()), observed)
        candidate = self.artifact(); candidate["summary"] = "Same input calculation, revised presentation"
        updated = operations.execute(self.root, "test-task", candidate, self.critical())
        for layer in ["application", "effect"]:
            self.assertEqual(updated[layer], {"status": "unknown"})
            stale = copy.deepcopy(updated); stale[layer] = observed[layer]
            with self.assertRaisesRegex(Rejected, "exact result"):
                validation.task(self.root, stale)
        archived = [h["detail"] for h in updated["history"] if h["action"] == "superseded-result"]
        self.assertEqual(archived[-1]["effect"], observed["effect"])
        validation.repository(self.root)

    def test_episode_denominator_and_cross_source_outcome(self):
        data = self.source([{"id": "one", "scenario": "appointment", "outcome": "completed"}, {"id": "two", "scenario": "appointment", "outcome": "waiting"}])
        self.assertEqual(operations.episodes(data, 3)["missing"], 1)
        with self.assertRaises(Rejected): operations.episodes(data, 1)
        other = copy.deepcopy(data); other["records"][0]["outcome"] = "unknown"
        self.assertEqual(operations.reconcile(data, other)["different"], ["one"])

    def test_decimal_sum_is_exact_without_business_rounding_rule(self):
        t = self.task()
        data = self.source([{"id": "one", "value": 0.1}, {"id": "two", "value": 0.2}]); write(self.root / "company/sources/input.json", data)
        candidate = self.artifact(); candidate["total"] = 0.3
        criterion = {**t["acceptance"], "expected_total": 0.3}
        self.assertTrue(validation.result(self.root, candidate, criterion))

    def test_task_id_semantic_conflict_preserves_original(self):
        t = self.task()
        with self.assertRaises(Rejected): operations.intake(self.root, "test-task", "Different request", "test-owner", t["acceptance"], True)
        self.assertEqual(load(task_file(self.root, "test-task"))["request"], t["request"])

    def test_workflow_disabled_missing_capability_unknown_version_and_index(self):
        self.assertTrue(validation.self_test()["correct_example"])
        self.assertEqual(operations.tick(self.root)["status"], "disabled")
        meta = load(self.root / "release.yaml"); meta["state_format"] = 2; write(self.root / "release.yaml", meta)
        with self.assertRaisesRegex(Rejected, "unsupported release"): validation.repository(self.root)
        meta["state_format"] = 1; write(self.root / "release.yaml", meta)
        unknown = self.root / "standards/unknown-method.md"; unknown.write_text('---\nid: unknown-method\nversion: 1\nowner: test-owner\n---\nUnindexed standalone method\n', encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "missing from map"): validation.repository(self.root)

    def test_marketing_and_research_contracts_preserve_unknowns(self):
        self.input()
        common = self.artifact()
        marketing = {**common, "kind": "marketing", "audience": "Synthetic recipient", "recipient_action": "Read draft", "material": "Draft only", "measurement_source": "Declared test source", "neighbor_handoff": "Recipient can follow source", "proposal_status": "draft", "budget_status": "unknown", "publication_status": "not-authorized", "application_status": "unknown", "effect_status": "unknown"}
        self.assertTrue(validation.result(self.root, marketing, {"kind": "marketing"}))
        marketing["effect_status"] = "observed"
        with self.assertRaises(Rejected): validation.result(self.root, marketing, {"kind": "marketing"})
        research = {**common, "kind": "research", "question": "What does the test source contain?", "coverage": "One authorized local file", "findings": [{"claim": "Two rows", "source": "company/sources/input.json", "level": "observation"}]}
        self.assertTrue(validation.result(self.root, research, {"kind": "research"}))
        research["findings"][0]["source"] = "invented"
        with self.assertRaises(Rejected): validation.result(self.root, research, {"kind": "research"})


class SourcesAndBackground(Fixture):
    def test_real_file_export_and_wrong_company_partial_stale(self):
        self.input(); self.export_config()
        self.assertEqual(connectors.read(self.root, "test-data")["evidence_level"], "file-export")
        original = self.source()
        for field, value in [("company_id", "wrong"), ("account", "wrong"), ("complete", False), ("observed_at", "2000-01-01T00:00:00+00:00")]:
            candidate = copy.deepcopy(original); candidate[field] = value; write(self.root / "company/sources/input.json", candidate)
            with self.subTest(field=field), self.assertRaises(Rejected): connectors.read(self.root, "test-data")
        with self.assertRaises(Rejected): connectors.read(self.root, "missing")

    def test_http_pagination_429_redirect_and_limit(self):
        data = self.source()
        mode = {"value": "normal"}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(server):
                if mode["value"] == "429": server.send_response(429); server.end_headers(); return
                if mode["value"] == "redirect": server.send_response(302); server.send_header("Location", "http://example.invalid/private"); server.end_headers(); return
                result = copy.deepcopy(data)
                result["records"] = data["records"][1:] if "cursor=" in server.path else data["records"][:1]
                result["next_cursor"] = "page-two" if "cursor=" not in server.path or mode["value"] == "loop" else None
                server.send_response(200); server.end_headers(); server.wfile.write(json.dumps(result).encode())
            def log_message(server, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.export_config("http", endpoint=f"http://127.0.0.1:{server.server_port}/records")
        self.assertEqual(connectors.read(self.root, "test-data")["pages"], 2)
        for item in ["429", "redirect", "loop"]:
            mode["value"] = item
            with self.subTest(item=item), self.assertRaises((ValueError, Rejected)): connectors.read(self.root, "test-data")

    def test_stdio_mcp_negotiation_read_tool_and_error(self):
        data = self.source()
        program = self.base / "read-server.py"
        program.write_text('import json,sys\nenvelope='+repr(data)+'\nfor line in sys.stdin:\n m=json.loads(line)\n if "id" not in m: continue\n method=m["method"]\n if method=="initialize": result={"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"fixture","version":"1"}}\n elif method=="tools/list": result={"tools":[{"name":"read_records","inputSchema":{"type":"object"}}]}\n elif method=="tools/call": result={"structuredContent":envelope}\n else: result={}\n print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":result}),flush=True)\n', encoding="utf-8")
        self.export_config("mcp", command=[sys.executable, str(program)], read_tool="read_records", allowed_read_tools=["read_records"])
        self.assertEqual(len(connectors.read(self.root, "test-data")["records"]), 2)
        cfg = config(self.root); cfg["sources"]["test-data"]["read_tool"] = "write_records"; write(self.root / "company/config.yaml", cfg)
        with self.assertRaisesRegex(Rejected, "not allowed"): connectors.read(self.root, "test-data")

    def test_graph_current_source_aliases_and_disabled_fallback(self):
        source = {"schema_version": 1, "id": "stock-handbook", "owner": "test-owner", "origin": "Synthetic authorized handbook", "status": "accepted", "aliases": ["остаток на складе"], "summary": "Count 7", "links": [{"to": "design-basis", "type": "supports", "level": "hypothesis"}]}
        file = self.root / "company/sources/stock-handbook/source.yaml"; write(file, source)
        first = knowledge.search(self.root, "остаток на складе")["hits"][0]
        self.assertIn("7", first["excerpt"])
        source["summary"] = "Count 9"; write(file, source)
        second = knowledge.search(self.root, "остаток на складе")["hits"][0]
        self.assertNotEqual(first["sha256"], second["sha256"])
        cfg = config(self.root); cfg["features"]["graph"] = False; write(self.root / "company/config.yaml", cfg)
        self.assertEqual(knowledge.search(self.root, "остаток на складе")["mode"], "direct-source")
        with self.assertRaises(Rejected): knowledge.read_kb(self.root, "missing")
        source["links"][0]["type"] = "causes"; write(file, source)
        with self.assertRaises(Rejected): knowledge.search(self.root, "остаток на складе")

    def test_one_shot_ticks_change_only_limits_off_and_recovery(self):
        self.assertFalse(operations.tick(self.root)["notify"])
        self.export_config()  # Accepted sources precede pinning the monitor's methods/config.
        cfg = config(self.root); cfg["features"]["proactive"] = True; cfg["proactive"] = {"authorization": "accepted test-only ticks", "task_id": "monitor-task", "max_runs": 3, "interval_seconds": 1}; write(self.root / "company/config.yaml", cfg)
        operations.intake(self.root, "monitor-task", "Synthetic monitor without external actions", "test-owner", {"kind": "action-summary"}, True)
        self.task()
        first = self.cli("tick"); self.assertTrue(first["notify"])
        self.assertEqual(self.cli("tick")["status"], "too-early")
        t = load(task_file(self.root, "monitor-task")); t["automation"]["last_at"] = "2000-01-01T00:00:00+00:00"; write(task_file(self.root, "monitor-task"), t)
        self.assertFalse(self.cli("tick")["notify"])
        t = load(task_file(self.root, "monitor-task")); t["automation"]["runs"] = 3; write(task_file(self.root, "monitor-task"), t)
        self.assertEqual(self.cli("tick")["status"], "limit")
        cfg["features"]["proactive"] = False; write(self.root / "company/config.yaml", cfg)
        self.assertEqual(self.cli("tick")["status"], "disabled")


class GitAcceptance(Fixture):
    def test_release_guide_allowed_but_development_history_rejected(self):
        source = self.base / "release-source"; source.mkdir()
        delivery.git(source, "init", "-b", "main"); delivery.identity(source)
        (source / "docs").mkdir()
        (source / "docs/company-system-guide.html").write_text("Synthetic portable guide", encoding="utf-8")
        delivery.git(source, "add", "."); delivery.git(source, "commit", "-m", "Allowed guide")
        release = self.base / "release.git"
        delivery.git(source, "clone", "--bare", str(source), str(release))
        self.assertEqual(lifecycle.release_isolated(release)["policy"], "release")
        for number, forbidden in enumerate(("docs/development/PLAN.md", "docs/private.html")):
            with self.subTest(path=forbidden):
                delivery.git(source, "checkout", "-b", f"forbidden-{number}", "main")
                file = source / forbidden; file.parent.mkdir(parents=True, exist_ok=True); file.write_text("Private development", encoding="utf-8")
                delivery.git(source, "add", "--", forbidden); delivery.git(source, "commit", "-m", "Forbidden guide sibling")
                invalid = self.base / f"invalid-{number}.git"
                delivery.git(source, "clone", "--bare", str(source), str(invalid))
                with self.assertRaisesRegex(Rejected, "non-template history"):
                    lifecycle.release_isolated(invalid)

    def product_release(self):
        p = self.base / "product"; p.mkdir()
        shutil.copytree(TEMPLATE, p / "template", symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        (p / "PRIVATE-DEVELOPMENT.md").write_text("Do not send product development to companies\n", encoding="utf-8")
        self.seed(p)
        release = self.base / "release.git"
        product.release(p, release)
        return p, release

    def test_portable_feature_release_updates_client_without_development_or_data_loss(self):
        p, release = self.product_release()
        company = self.base / "client"
        lifecycle.create(release, company, "client-company", "client-owner")
        (company / "work").mkdir(exist_ok=True)
        (company / "work/kept.txt").write_text("Client-owned work\n", encoding="utf-8")
        delivery.commit(company, ["work/kept.txt"], "Retain client data")
        original_config = config(company)
        company_base = delivery.git(company, "rev-parse", "HEAD")
        remote = self.base / "client-common.git"
        delivery.git(company, "clone", "--bare", str(company), str(remote))

        template = p / "template"
        feature_id = "company-new-feature"
        feature = template / "skills" / feature_id
        shutil.copytree(template / "skills/company-context", feature)
        skill_file = feature / "SKILL.md"
        skill_file.write_text(skill_file.read_text(encoding="utf-8").replace("name: company-context", "name: " + feature_id), encoding="utf-8")
        recipe = load(feature / "workflow.yaml")
        recipe["id"] = feature_id
        write(feature / "workflow.yaml", recipe)
        skill_map = template / "skills/README.md"
        skill_map.write_text(skill_map.read_text(encoding="utf-8") + f"\n[Synthetic new feature]({feature_id}/SKILL.md).\n", encoding="utf-8")
        for directory in (".agents/skills", ".claude/skills"):
            (template / directory / feature_id).symlink_to("../../skills/" + feature_id, target_is_directory=True)
        new_script = template / "scripts/synthetic-feature.py"
        new_script.write_text("print('New synthetic feature available')\n", encoding="utf-8")
        meta = load(template / "release.yaml")
        parts = meta["version"].split(".")
        meta["version"] = ".".join([*parts[:2], str(int(parts[2]) + 1)])
        write(template / "release.yaml", meta)
        validation.repository(template)
        delivery.git(p, "add", "template")
        delivery.git(p, "commit", "-m", "Release new synthetic feature")
        target = product.release(p, release)
        bundle = self.base / "feature.bundle"
        exported = product.package(release, bundle)
        self.assertEqual(exported["release_sha"], target)
        self.assertEqual(exported["bundle_sha256"], digest(bundle))
        received = self.base / "received-release.git"
        delivery.git(self.base, "clone", "--bare", str(bundle), str(received))
        lifecycle.release_isolated(received)
        self.assertNotIn("PRIVATE-DEVELOPMENT", delivery.git(received, "rev-list", "--objects", "--all"))
        updated = lifecycle.update(company, received, self.base / "client-update")
        self.assertEqual(updated["status"], "verified-candidate", updated)
        candidate = Path(updated["candidate"])
        self.assertEqual(config(candidate), original_config)
        self.assertEqual((candidate / "work/kept.txt").read_text(encoding="utf-8"), "Client-owned work\n")
        self.assertTrue((candidate / "skills" / feature_id / "SKILL.md").exists())
        self.assertEqual(subprocess.check_output([sys.executable, str(candidate / "scripts/synthetic-feature.py")], text=True).strip(), "New synthetic feature available")
        delivered = delivery.deliver(candidate, str(remote), expected_base=company_base)
        self.assertEqual(delivered["status"], "readback-confirmed")
        self.assertEqual(lifecycle.update(candidate, received, self.base / "repeat-update")["status"], "already-installed")
        saved = bundle.read_bytes()
        with self.assertRaisesRegex(Rejected, "no overwrite"):
            product.package(release, bundle)
        self.assertEqual(bundle.read_bytes(), saved)
        broken = self.base / "broken.bundle"
        broken.symlink_to("absent-bundle-target")
        with self.assertRaisesRegex(Rejected, "no overwrite"):
            product.package(release, broken)
        with self.assertRaises(Rejected):
            product.package(p / ".git", self.base / "forbidden.bundle")
        self.assertFalse((self.base / "forbidden.bundle").exists())

    def test_copy_update_two_companies_history_and_local_rule(self):
        p, release = self.product_release()
        a, b = self.base / "alpha", self.base / "beta"
        lifecycle.create(release, a, "alpha-company", "test-owner")
        lifecycle.create(release, b, "beta-company", "test-owner")
        local = "company/standards/local-rule.md"
        (a / local).parent.mkdir(parents=True)
        (a / local).write_text('---\nid: local-rule\nversion: 1\nowner: test-owner\n---\n# Alternative output arrangement\nAccepted; same criterion, explicit reason and tested examples.\n', encoding="utf-8")
        cfg = config(a); cfg["bindings"]["execute"] = {"base": "standards/task-validation.md", "base_sha256": digest(a / "standards/task-validation.md"), "local": local, "approved_by": "test-owner", "reason": "Different format; guarantees retained", "checks": "same good/bad examples"}; write(a / "company/config.yaml", cfg)
        delivery.commit(a, [local, "company/config.yaml"], "Accept local alternative")
        for company in [a,b]:
            (company / "work").mkdir(exist_ok=True); (company / "work/retained.txt").write_text("Synthetic company work retained\n", encoding="utf-8"); delivery.commit(company, ["work/retained.txt"], "Retain work")
        release_meta = load(p / "template/release.yaml"); release_meta["version"] = ".".join([*release_meta["version"].split(".")[:2], str(int(release_meta["version"].split(".")[2]) + 1)]); write(p / "template/release.yaml", release_meta)
        method = p / "template/standards/runtime.md"; method.write_text(method.read_text(encoding="utf-8") + "\nTechnical documentation improvement\n", encoding="utf-8")
        delivery.git(p, "add", "template"); delivery.git(p, "commit", "-m", "New clean release"); product.release(p, release)
        for company in [a,b]:
            updated = lifecycle.update(company, release, self.base / (company.name + "-updated"))
            self.assertEqual(updated["status"], "verified-candidate", updated)
            candidate = Path(updated["candidate"])
            self.assertEqual((candidate / "work/retained.txt").read_text(encoding="utf-8"), "Synthetic company work retained\n")
            self.assertEqual(config(candidate), config(company))
            self.assertEqual(lifecycle.update(candidate, release, self.base / (company.name + "-repeat"))["status"], "already-installed")
            objects = delivery.git(candidate, "rev-list", "--objects", "--all")
            self.assertNotIn("PRIVATE-DEVELOPMENT", objects)
            if company == a: self.assertTrue((candidate / local).is_file())
        with self.assertRaises(Rejected): lifecycle.create(release, a, "third-company", "test-owner")

    def test_semantic_binding_change_stops_even_text_merge_clean(self):
        p, release = self.product_release(); a = self.base / "alpha"; lifecycle.create(release,a,"alpha-company","test-owner")
        local = a / "company/standards/local-rule.md"; local.parent.mkdir(); local.write_text('---\nid: local-rule\nversion: 1\nowner: test-owner\n---\nAccepted alternative\n', encoding="utf-8")
        cfg = config(a); cfg["bindings"]["execute"] = {"base": "standards/task-validation.md", "base_sha256": digest(a / "standards/task-validation.md"), "local": "company/standards/local-rule.md", "approved_by": "test-owner", "reason": "Accepted method", "checks": "examples"}; write(a / "company/config.yaml",cfg); delivery.commit(a,["company/config.yaml","company/standards/local-rule.md"],"Local rule")
        method = p / "template/standards/task-validation.md"; method.write_text(method.read_text(encoding="utf-8")+"\nNew semantic requirement\n", encoding="utf-8");delivery.git(p,"add","template");delivery.git(p,"commit","-m","Change method");product.release(p,release)
        result = lifecycle.update(a,release,self.base/"semantic-candidate")
        self.assertEqual(result["status"],"needs-reconciliation")
        self.assertIn("semantic reconciliation", " ".join(result["issues"]))

    def test_common_delivery_concurrent_results_stale_and_conflict(self):
        self.seed(); remote = self.base / "common.git"; delivery.git(self.root,"clone","--bare",str(self.root),str(remote))
        a,b,c = [self.base/x for x in ["session-a","session-b","session-c"]]
        for dest in [a,b]: delivery.prepare(self.root,str(remote),"main",dest)
        base = delivery.git(a,"rev-parse","HEAD")
        (a/"work").mkdir(exist_ok=True); (a/"work/a.txt").write_text("A", encoding="utf-8");delivery.commit(a,["work/a.txt"],"A result")
        delivery.deliver(a,str(remote),expected_base=base)
        (b/"work").mkdir(exist_ok=True); (b/"work/b.txt").write_text("B", encoding="utf-8");delivery.commit(b,["work/b.txt"],"B result")
        with self.assertRaisesRegex(Rejected,"stale"):delivery.deliver(b,str(remote),expected_base=base)
        delivery.git(b,"fetch","origin");delivery.git(b,"merge","--no-edit","origin/main");delivery.deliver(b,str(remote))
        self.assertTrue(delivery.deliver(b,str(remote))["repeated"])
        delivery.prepare(self.root,str(remote),"main",c)
        self.assertEqual((c/"work/a.txt").read_text(encoding="utf-8"),"A");self.assertEqual((c/"work/b.txt").read_text(encoding="utf-8"),"B")
        delivery.git(a,"fetch","origin");delivery.git(a,"merge","--no-edit","origin/main")
        for company,word in [(a,"one"),(b,"two")]:
            file=company/"company/projects/README.md";file.write_text(word, encoding="utf-8");delivery.commit(company,["company/projects/README.md"],word)
        delivery.deliver(a,str(remote))
        delivery.git(b,"fetch","origin");merged=delivery.git(b,"merge","--no-edit","origin/main",check=False)
        self.assertNotEqual(merged.returncode,0)
        self.assertIn("one",(b/"company/projects/README.md").read_text(encoding="utf-8"));self.assertIn("two",(b/"company/projects/README.md").read_text(encoding="utf-8"))

    def test_result_delivery_ack_and_lost_push_response_readback(self):
        self.task(); operations.execute(self.root,"test-task",self.artifact(),self.critical());self.seed()
        remote=self.base/"common.git";delivery.git(self.root,"clone","--bare",str(self.root),str(remote))
        receipt=delivery.deliver(self.root,str(remote),task_id="test-task")
        t=load(task_file(self.root,"test-task"));self.assertEqual(t["delivery"]["status"],"delivered");self.assertTrue(t["delivery"]["readback"])
        (self.root/"work/extra.txt").write_text("Next permitted result", encoding="utf-8");delivery.commit(self.root,["work/extra.txt"],"Next")
        original=delivery.git
        def lost(root,*args,**kwargs):
            r=original(root,*args,**kwargs)
            if args and args[0]=="push" and not kwargs.get("check",True):r.returncode=1;r.stderr="synthetic lost response after successful push"
            return r
        with patch.object(delivery,"git",side_effect=lost):self.assertEqual(delivery.deliver(self.root,str(remote))["status"],"readback-confirmed")

    def test_backup_restore_and_corruption_dirty_tree(self):
        self.task();operations.execute(self.root,"test-task",self.artifact(),self.critical());self.seed()
        backup=self.base/"backup";lifecycle.backup(self.root,backup);restored=self.base/"restored";self.assertEqual(lifecycle.restore(backup,restored)["status"],"restored-and-verified")
        self.assertEqual(load(task_file(restored,"test-task")),load(task_file(self.root,"test-task")))
        with (backup/"history.bundle").open("ab") as f:f.write(b'corrupt')
        with self.assertRaisesRegex(Rejected,"corrupted"):lifecycle.restore(backup,self.base/"corrupt-restore")
        (self.root/"work/dirty.txt").write_text("Preserve unsaved work", encoding="utf-8")
        with self.assertRaisesRegex(Rejected,"dirty"):lifecycle.backup(self.root,self.base/"dirty-backup")
        self.assertEqual((self.root/"work/dirty.txt").read_text(encoding="utf-8"),"Preserve unsaved work")

    def test_sanitized_proposal_exact_permission_no_company_history(self):
        package=self.base/"package";file=package/"standards/general-method.md";file.parent.mkdir(parents=True);file.write_text("An independently authored general procedure with a clean example\n", encoding="utf-8")
        approval={"approved_by":"test-owner","permission":"share-sanitized-method","purpose":"General reusable method","files":{"standards/general-method.md":digest(file)}};write(package/"approval.json",approval)
        target=self.base/"proposal";self.assertEqual(lifecycle.proposal(self.root,package,target)["status"],"sanitized-candidate");self.assertFalse((target/".git").exists())
        file.write_text("test-company private data", encoding="utf-8")
        with self.assertRaises(Rejected):lifecycle.proposal(self.root,package,self.base/"rejected-proposal")

    def test_proposal_rejects_noncanonical_paths_before_copy_or_candidate(self):
        package = self.base / "malicious-package"
        (package / "standards").mkdir(parents=True)
        file = package / "company/private-note.txt"; file.parent.mkdir(); file.write_text("Generic-looking contents", encoding="utf-8")
        for index, relative in enumerate(["standards/../company/private-note.txt", "standards/./method.md", "standards//method.md", "standards/../../outside.md"]):
            write(package / "approval.json", {"approved_by": "test-owner", "permission": "share-sanitized-method", "purpose": "Boundary test", "files": {relative: digest(file)}})
            dest = self.base / f"bad-proposal-{index}"
            with self.assertRaisesRegex(Rejected, "canonical relative"):
                lifecycle.proposal(self.root, package, dest)
            self.assertFalse(dest.exists())
        p, release = self.product_release()
        remote = self.base / "proposal-product.git"; delivery.git(p, "clone", "--bare", str(p), str(remote))
        write(package / "approval.json", {"approved_by": "test-owner", "permission": "share-sanitized-method", "purpose": "Boundary test", "files": {"standards/../company/private-note.txt": digest(file)}})
        dest = self.base / "bad-product-candidate"
        with self.assertRaisesRegex(Rejected, "canonical relative"):
            lifecycle.proposal_candidate(self.root, package, p, str(remote), dest)
        self.assertFalse(dest.exists())

    def test_ordinary_summary_snapshot_validates_delivers_and_survives_source_changes(self):
        source_task = self.task(confirmed=False)
        operations.intake(self.root, "summary-task", "Покажи действия, ответственных и ожидания", "test-owner", {"kind": "action-summary"}, True)
        artifact = self.cli("summary", "--task", "summary-task")
        self.assertEqual([entry["id"] for entry in artifact["entries"]], ["test-task"])
        critical = {"request_alignment": "Shows recorded actions, owner and exact blocker", "counterexample": "A summary referring to its own mutable task would become invalid", "limitations": ["Snapshot of recorded revisions"], "references": [artifact["sources"][0]["path"]]}
        operations.execute(self.root, "summary-task", artifact, critical)
        validation.repository(self.root)
        self.seed(); remote = self.base / "summary-common.git"; delivery.git(self.root, "clone", "--bare", str(self.root), str(remote))
        delivery.deliver(self.root, str(remote), task_id="summary-task")
        validation.repository(self.root)
        operations.decide(self.root, "test-task", source_task["revision"], "confirm", "Owner resolved pending request", "test-owner")
        operations.execute(self.root, "test-task", self.artifact(), self.critical())
        validation.repository(self.root)
        snapshot = self.root / artifact["sources"][0]["path"]
        changed = load(snapshot); changed["entries"][0]["status"] = "cancelled"; write(snapshot, changed)
        with self.assertRaisesRegex(Rejected, "source changed"):
            validation.repository(self.root)

    def test_full_company_path_update_rollback_keeps_new_work_restore_and_fresh_session(self):
        p, release = self.product_release()
        a = self.base / "company-alpha"
        lifecycle.create(release, a, "alpha-company", "test-owner")
        source = "company/sources/design-basis/source.yaml"
        operations.intake(a, "first-process", "Prepare source-backed internal note", "test-owner", {"kind": "note"}, True, inputs=[source])
        artifact = {"schema_version": 1, "company_id": "alpha-company", "kind": "note", "summary": "A single canonical source supports the internal note", "next_action": "Next participant reads this result and original source", "limitations": ["Synthetic company; no field/business effect"], "sources": [{"path": source, "sha256": digest(a/source)}]}
        critical = {"request_alignment": "The note answers the accepted request using the original source", "counterexample": "A derived graph alone would not prove source provenance", "limitations": [], "references": [source]}
        result = operations.execute(a, "first-process", artifact, critical)
        delivery.commit(a, ["work/first-process/task.json", result["output"]["path"]], "Validated first internal process")
        remote = self.base / "alpha-common.git"; delivery.git(a, "clone", "--bare", str(a), str(remote))
        delivery.deliver(a, str(remote), task_id="first-process")
        note = p / "template/standards/runtime.md"; note.write_text(note.read_text(encoding="utf-8") + "\nNew method documentation; same result criteria.\n", encoding="utf-8")
        meta = load(p / "template/release.yaml"); meta["version"] = ".".join([*meta["version"].split(".")[:2], str(int(meta["version"].split(".")[2]) + 1)]); write(p / "template/release.yaml", meta)
        delivery.git(p, "add", "template"); delivery.git(p, "commit", "-m", "Method release"); product.release(p, release)
        updated = lifecycle.update(a, release, self.base / "update-candidate")
        self.assertEqual(updated["status"], "verified-candidate", updated)
        candidate = Path(updated["candidate"])
        delivery.deliver(candidate, str(remote))
        self.assertEqual(self.cli("context", "--task", "first-process", root=candidate)["tasks"][0]["status"], "verified")
        (candidate / "work/new-result.txt").write_text("Created after update; rollback must preserve it\n", encoding="utf-8")
        delivery.commit(candidate, ["work/new-result.txt"], "New company work after upgrade")
        rolled = lifecycle.rollback(candidate, self.base / "rollback-candidate")
        self.assertEqual(rolled["status"], "verified-candidate", rolled)
        rollback = Path(rolled["candidate"])
        self.assertTrue((rollback / "work/new-result.txt").exists())
        self.assertEqual(load(rollback / "release.yaml")["version"], load(TEMPLATE / "release.yaml")["version"])
        backup = self.base / "complete-backup"; lifecycle.backup(rollback, backup)
        restored = self.base / "complete-restored"; lifecycle.restore(backup, restored)
        self.assertEqual(self.cli("context", "--task", "first-process", root=restored)["tasks"][0]["next_action"], artifact["next_action"])
        self.assertTrue((restored / "work/new-result.txt").exists())

    def test_company_method_proposal_uses_same_product_delivery_without_data(self):
        p, release = self.product_release()
        remote = self.base / "product-common.git"; delivery.git(p, "clone", "--bare", str(p), str(remote))
        package = self.base / "sanitized-package"
        file = package / "standards/general-procedure.md"; file.parent.mkdir(parents=True)
        file.write_text('---\nid: general-procedure\nversion: 1\nowner: product-maintainer\nstatus: proposal\n---\n# Generic procedure\nOwn deliberately anonymized method with independent clean sample; applicability pending.\n', encoding="utf-8")
        write(package / "approval.json", {"approved_by": "test-owner", "permission": "share-sanitized-method", "purpose": "Share a generic proposal only", "files": {"standards/general-procedure.md": digest(file)}})
        candidate = lifecycle.proposal_candidate(self.root, package, p, str(remote), self.base / "general-candidate")
        delivery.commit(Path(candidate["path"]), candidate["paths"], "Share sanitized proposal; method acceptance pending")
        receipt = delivery.deliver(Path(candidate["path"]), str(remote), expected_base=candidate["base"])
        self.assertEqual(receipt["status"], "readback-confirmed")
        copied = self.base / "product-reader"; delivery.prepare(p, str(remote), "main", copied)
        self.assertTrue((copied / "template/standards/general-procedure.md").exists())
        self.assertNotIn("test-company", (copied / "template/standards/general-procedure.md").read_text(encoding="utf-8"))
        self.assertFalse((copied / "work").exists())


if __name__ == "__main__":
    unittest.main()
