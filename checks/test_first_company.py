"""First installation through actual release packaging and fresh CLI processes.

Only synthetic companies/local bare remotes; no shared source refs or live accounts.
The full route requires the settled revision/hash mutation preconditions.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


PRODUCT = Path(__file__).resolve().parents[1]


class FirstCompany(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cws-first-company-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.product = self.base / "product"
        self.product.mkdir()
        for directory in ("template", "scripts"):
            shutil.copytree(PRODUCT / directory, self.product / directory, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        from checks.fixture_helpers import directory_projections
        directory_projections(self.product / "template")
        # A tracked private sentinel proves split history, not just clean HEAD.
        private = self.product / "docs/development/PRIVATE-SENTINEL.md"
        private.parent.mkdir(parents=True)
        private.write_text("Synthetic maintainer notes must never reach a company.\n", encoding="utf-8")
        self.git(self.product, "init", "-b", "main")
        self.git(self.product, "add", ".")
        self.git(self.product, "commit", "-m", "Synthetic current product")
        self.assertEqual(self.git(self.product, "status", "--porcelain"), "")
        self.assert_origin(self.product / "template")
        self.release = self.base / "release.git"
        self.received = self.base / "received.git"
        bundle = self.base / "first.bundle"
        packaged = self.package(bundle)
        self.assertEqual(packaged["bundle_sha256"], self.sha(bundle))
        self.git(self.base, "clone", "--mirror", str(bundle), str(self.received))
        self.assertEqual(self.git(self.received, "rev-parse", "refs/heads/main"), packaged["release_sha"])
        objects = self.git(self.received, "rev-list", "--objects", "--all")
        for forbidden in ("PRIVATE-SENTINEL", "docs/development", "factory-workspaces", "evidence/evals", "template/"):
            self.assertNotIn(forbidden, objects)
        self.root = self.base / "company"
        created = self.cli(self.product / "template", "create", str(self.received), str(self.root),
                           "--id", "demo-company", "--owner", "demo-owner")
        self.assertEqual(created["base"], packaged["release_sha"])
        self.assert_origin(self.root)
        for name in ("system.py", "operations.py", "lifecycle.py"):
            self.assertEqual((self.root / "scripts" / name).read_bytes(),
                             (PRODUCT / "template/scripts" / name).read_bytes())
        self.remote = self.base / "company-common.git"
        self.git(self.base, "clone", "--bare", str(self.root), str(self.remote))
        self.git(self.root, "remote", "set-url", "origin", str(self.remote))

    def run_process(self, root, arguments, code=0):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(arguments, cwd=root, env=environment, capture_output=True,
                                text=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, code, result.stdout + "\n" + result.stderr)
        return result

    def git(self, root, *args):
        return self.run_process(root, ["git", "-c", "user.name=Company Work System",
                                      "-c", "user.email=local-system@example.invalid", *args]).stdout.strip()

    def cli(self, root, *args, code=0):
        result = self.run_process(root, [sys.executable, str(root / "scripts/system.py"),
                                         "--root", str(root), *args], code)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def package(self, bundle):
        result = self.run_process(self.product, [sys.executable, str(self.product / "scripts/product.py"),
                                                  str(self.release), "--bundle", str(bundle)])
        self.assertEqual(self.git(self.product, "status", "--porcelain"), "")
        return json.loads(result.stdout)

    def assert_origin(self, root):
        script = ("import json, pathlib, sys; sys.path.insert(0, sys.argv[1]); "
                  "import core, operations, lifecycle; "
                  "print(json.dumps([str(pathlib.Path(m.__file__).resolve()) "
                  "for m in (core, operations, lifecycle)]))")
        actual = self.run_process(root, [sys.executable, "-c", script, str(root / "scripts")])
        self.assertEqual(json.loads(actual.stdout),
                         [str((root / "scripts" / name).resolve())
                          for name in ("core.py", "operations.py", "lifecycle.py")])

    @staticmethod
    def sha(file):
        return hashlib.sha256(file.read_bytes()).hexdigest()

    @staticmethod
    def save(file, value):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def cfg(self):
        return yaml.safe_load((self.root / "company/config.yaml").read_text(encoding="utf-8"))

    def save_cfg(self, cfg):
        (self.root / "company/config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def tracked(self, root, protected=False):
        inventory = {}
        for name in self.git(root, "ls-files", "-z").split("\0"):
            if name and (not protected or name.startswith(("company/", "work/"))):
                file = root / name
                inventory[name] = ("symlink", os.readlink(file)) if file.is_symlink() else ("file", file.read_bytes())
        return inventory

    def restore_exact(self, root):
        before = self.tracked(root)
        backup = self.base / "backup"
        self.cli(root, "backup", str(backup))
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["schema_version"], 1)
        restored = self.base / "restored"
        self.assertEqual(self.cli(root, "restore", str(backup), str(restored))["status"], "restored-and-verified")
        self.assertEqual(self.tracked(restored), before)
        self.assertEqual(self.git(restored, "rev-parse", "HEAD"), self.git(root, "rev-parse", "HEAD"))
        self.cli(restored, "validate")
        self.assertEqual(self.cli(restored, "doctor")["status"], "ready")
        self.cli(root, "restore", str(backup), str(restored), code=2)
        self.assertEqual(self.tracked(restored), before)
        return restored

    def test_bundle_create_doctor_permissions_update_and_restore(self):
        """Baseline-feasible deployment/recovery subcase, without new execute API."""
        self.assertEqual(self.cli(self.root, "doctor")["status"], "ready")
        cfg = self.cfg()
        self.assertEqual((cfg["id"], cfg["owner"]), ("demo-company", "demo-owner"))
        self.assertTrue(cfg["permissions"]["local_work"])
        self.assertFalse(cfg["permissions"]["external_delivery"])
        self.assertFalse(cfg["features"]["proactive"])
        self.assertFalse(cfg.get("hooks", {}).get("enabled", False))
        for field in ("sources", "definitions", "bindings"):
            self.assertEqual(cfg[field], {})
        self.assertEqual(self.cli(self.root, "preflight")["status"], "ready")
        criterion = self.root / "work/unknown-task/acceptance.json"
        self.save(criterion, {"kind": "action-summary"})
        cfg["permissions"]["local_work"] = False
        self.save_cfg(cfg)
        denied = self.cli(self.root, "intake", "denied-task", "--request", "No permission", "--owner", "demo-owner",
                          "--acceptance", str(criterion), code=2)
        self.assertIn("authorized", denied["reason"])
        self.assertFalse((self.root / "work/denied-task/task.json").exists())
        cfg["permissions"]["local_work"] = True
        self.save_cfg(cfg)
        waiting = self.cli(self.root, "intake", "unknown-task", "--request", "Unknown process owner decision",
                           "--owner", "demo-owner", "--acceptance", str(criterion), "--confirmed",
                           "--unknown", "Owner must define the scope and KPI; no inference")
        self.assertEqual(waiting["status"], "waiting")
        cancelled = self.cli(self.root, "decide", "unknown-task", "cancel", "--revision", str(waiting["revision"]),
                             "--actor", "demo-owner", "--reason", "Finish synthetic negative case without inventing definitions")
        self.assertEqual(cancelled["unknowns"], waiting["unknowns"])
        self.cli(self.root, "commit", "company/config.yaml", "work/unknown-task", "--message", "Keep owner unknowns")
        self.cli(self.root, "deliver", str(self.remote), "--expected-base", self.git(self.remote, "rev-parse", "main"))
        protected = self.tracked(self.root, protected=True)
        # Add a genuine descendant release, using the existing product packager.
        readme = self.product / "template/README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nSynthetic method documentation update.\n", encoding="utf-8")
        self.git(self.product, "add", "template/README.md")
        self.git(self.product, "commit", "-m", "Synthetic method release")
        next_bundle = self.base / "next.bundle"
        self.package(next_bundle)
        next_release = self.base / "next.git"
        self.git(self.base, "clone", "--mirror", str(next_bundle), str(next_release))
        updated = self.cli(self.root, "update", str(next_release), str(self.base / "updated"))
        self.assertEqual(updated["status"], "verified-candidate", updated)
        candidate = Path(updated["candidate"])
        self.assertEqual(self.tracked(candidate, protected=True), protected)
        self.assertEqual(self.tracked(self.root, protected=True), protected)
        self.assertEqual(self.cli(candidate, "update", str(next_release), str(self.base / "repeat"))["status"], "already-installed")
        delivered = self.cli(candidate, "deliver", str(self.remote), "--expected-base", updated["company_base"])
        self.assertEqual(delivered["status"], "readback-confirmed")
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), self.git(candidate, "rev-parse", "HEAD"))
        self.restore_exact(candidate)

    def test_cli_task_summary_delivery_observations_and_new_session(self):
        """Combined release acceptance: revision/hash guards must be implemented."""
        self.assertEqual(self.cli(self.root, "preflight")["status"], "ready")
        source_path = "company/sources/input.json"
        source = {"schema_version": 1, "company_id": "demo-company", "account": "synthetic-export",
                  "scope": "synthetic-scope", "period": "synthetic-period", "unit": "test-units",
                  "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "complete": True,
                  "records": [{"id": "one", "value": 2}, {"id": "two", "value": 3}], "next_cursor": None}
        self.save(self.root / source_path, source)
        cfg = self.cfg()
        cfg["sources"]["test-data"] = {"type": "export", "path": source_path, "approved_by": "demo-owner",
                                      "account": source["account"], "scope": source["scope"], "period": source["period"],
                                      "unit": source["unit"], "max_age_seconds": 3600, "max_pages": 1, "timeout_seconds": 5}
        self.save_cfg(cfg)
        source_map = self.root / "company/sources/README.md"
        source_map.write_text(source_map.read_text(encoding="utf-8") + "\n[Synthetic input](input.json).\n", encoding="utf-8")
        self.cli(self.root, "commit", "company", "--message", "Configure accepted synthetic export")
        self.cli(self.root, "source-read", "test-data")
        directory = self.root / "work/first-task"
        criterion = {"kind": "metrics", "expected_count": 2, "expected_total": 5,
                     "period": source["period"], "unit": source["unit"]}
        self.save(directory / "acceptance.json", criterion)
        task = self.cli(self.root, "intake", "first-task", "--request", "Check two synthetic rows and total 5 test-units",
                        "--owner", "demo-owner", "--acceptance", str(directory / "acceptance.json"), "--confirmed",
                        "--input", source_path, "--source-id", "test-data", "--skill", "company-execute")
        artifact = {"schema_version": 1, "company_id": "demo-company", "kind": "metrics", "summary": "Two rows, total 5",
                    "next_action": "Owner checks whether to use the result", "limitations": ["Synthetic local export; no business proof"],
                    "sources": [{"path": source_path, "sha256": self.sha(self.root / source_path)}], "complete": True,
                    "period": source["period"], "unit": source["unit"], "records": source["records"], "total": 5}
        critical = {"request_alignment": "Both requested rows retained; arithmetic and provenance checked",
                    "counterexample": "Missing rows or invented scope could yield a misleading total",
                    "limitations": ["Application/effect unknown"], "references": [source_path]}
        self.save(directory / "candidate.json", artifact)
        self.save(directory / "critical.json", critical)
        unknown = self.cli(self.root, "intake", "unknown-task", "--request", "Unknown application permission",
                           "--owner", "demo-owner", "--acceptance", str(directory / "acceptance.json"),
                           "--confirmed", "--unknown", "Owner must decide whether this result may be used")
        unknown_file = self.root / "work/unknown-task/task.json"
        unknown_bytes = unknown_file.read_bytes()
        rejected = self.cli(self.root, "execute", "unknown-task", "--artifact", str(directory / "candidate.json"),
                            "--critical", str(directory / "critical.json"), "--expected-revision", str(unknown["revision"]), code=2)
        self.assertIn("unconfirmed", rejected["reason"])
        self.assertEqual(unknown_file.read_bytes(), unknown_bytes)
        self.cli(self.root, "decide", "unknown-task", "cancel", "--revision", str(unknown["revision"]),
                 "--actor", "demo-owner", "--reason", "Finish negative case; permission remains unknown")
        execute = ("execute", "first-task", "--artifact", str(directory / "candidate.json"),
                   "--critical", str(directory / "critical.json"))
        task = self.cli(self.root, *execute, "--expected-revision", str(task["revision"]))
        self.assertEqual(task["schema_version"], 1)
        self.assertEqual(task["status"], "verified")
        self.assertEqual(task["application"], {"status": "unknown"})
        self.assertEqual(task["effect"], {"status": "unknown"})
        self.assertEqual(self.sha(self.root / task["output"]["path"]), task["output"]["sha256"])
        repeat = self.cli(self.root, *execute, "--expected-revision", str(task["revision"]))
        self.assertEqual(repeat, task)
        base = self.git(self.remote, "rev-parse", "main")
        self.cli(self.root, "validate")
        self.cli(self.root, "commit", "work/first-task", "work/unknown-task", "--message", "Verify first task and preserve unknown permission")
        receipt = self.cli(self.root, "deliver", str(self.remote), "--expected-base", base, "--task", "first-task")
        self.assertEqual(receipt["status"], "readback-confirmed")
        task = json.loads((directory / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["delivery"]["status"], "delivered")
        self.assertEqual(self.git(self.remote, "show", "main:" + task["output"]["path"]),
                         (self.root / task["output"]["path"]).read_text(encoding="utf-8").strip())
        self.assertTrue(self.cli(self.root, "deliver", str(self.remote), "--task", "first-task")["repeated"])
        # Every CLI call is a new session; context recovers exact output and revision.
        context = self.cli(self.root, "context", "--task", "first-task")
        self.assertEqual(context["native_memory"], "not-used")
        self.assertEqual(context["tasks"][0]["output"], task["output"])
        self.assertEqual(context["tasks"][0]["revision"], task["revision"])
        summary_dir = self.root / "work/first-summary"
        self.save(summary_dir / "acceptance.json", {"kind": "action-summary"})
        owning = self.cli(self.root, "intake", "first-summary", "--request", "Summarize recorded actions",
                          "--owner", "demo-owner", "--acceptance", str(summary_dir / "acceptance.json"),
                          "--confirmed", "--skill", "company-summary")
        summary = self.cli(self.root, "summary", "--task", "first-summary")
        entry = next(e for e in summary["entries"] if e["id"] == "first-task")
        for field in ("delivery", "application", "effect", "output"):
            self.assertEqual(entry[field], task[field])
        snapshot = self.root / summary["sources"][0]["path"]
        snapshot_bytes = snapshot.read_bytes()
        self.assertEqual(json.loads(snapshot_bytes)["entries"], summary["entries"])
        self.save(summary_dir / "candidate.json", summary)
        self.save(summary_dir / "critical.json", critical)
        self.cli(self.root, "execute", "first-summary", "--artifact", str(summary_dir / "candidate.json"),
                 "--critical", str(summary_dir / "critical.json"), "--expected-revision", str(owning["revision"]))
        # Absence and negative outcome are evidence text, not new enum/positive success.
        for layer, evidence in (("application", "Owner checked synthetic meeting: result was not used; reason unknown"),
                                ("effect", "Synthetic measured duration increased from 10 to 12 minutes; causality unknown")):
            task = self.cli(self.root, "observe", "first-task", layer, "--evidence", evidence, "--actor", "demo-owner",
                            "--expected-revision", str(task["revision"]), "--expected-result-sha256", task["output"]["sha256"])
            self.assertEqual(task[layer]["status"], "observed")
            self.assertEqual(task[layer]["evidence"], evidence)
        self.assertEqual(snapshot.read_bytes(), snapshot_bytes)
        current = self.cli(self.root, "summary")
        self.assertEqual(next(e for e in current["entries"] if e["id"] == "first-task")["effect"], task["effect"])
        self.cli(self.root, "validate")
        base = self.git(self.remote, "rev-parse", "main")
        self.cli(self.root, "commit", "work/first-task", "work/first-summary", "--message", "Record exact-result observations and summary")
        self.cli(self.root, "deliver", str(self.remote), "--expected-base", base, "--task", "first-summary")
        restored = self.restore_exact(self.root)
        self.assertEqual(self.cli(restored, "context", "--task", "first-task")["tasks"][0]["output"], task["output"])
        self.assertEqual(self.cfg()["definitions"], {})


if __name__ == "__main__":
    unittest.main()
