"""Exact caller intent and independent result layers on isolated companies."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from checks.test_system import Fixture, TEMPLATE
from core import Rejected, digest, load, method_bindings, task_file, write
import delivery
import operations
import validation


class PrelaunchRuntime(Fixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(Path(operations.__file__).resolve(), TEMPLATE / "scripts/operations.py")

    def recorded_bytes(self):
        # Covers the complete task, history/errors inside it and every result/snapshot.
        return {file.relative_to(self.root).as_posix(): file.read_bytes()
                for file in (self.root / "work").rglob("*") if file.is_file()}

    def execute(self, task, artifact=None):
        return operations.execute(self.root, task["id"], artifact or self.artifact(),
                                  self.critical(), expected_revision=task["revision"])

    def observe(self, task, layer="application", evidence="Owner used the exact result"):
        return operations.observe(self.root, task["id"], layer, evidence, "test-owner",
                                  expected_revision=task["revision"],
                                  expected_result_sha256=task["output"]["sha256"])

    def reject_unchanged(self, call, message=None):
        before = self.recorded_bytes()
        with self.assertRaisesRegex(Rejected, message or ".*"):
            call()
        self.assertEqual(self.recorded_bytes(), before)

    def command(self, *args):
        return subprocess.run([sys.executable, str(self.root / "scripts/system.py"),
                               "--root", str(self.root), *args],
                              capture_output=True, text=True, timeout=30)

    def test_execute_missing_token_preserves_every_byte(self):
        self.task()
        self.reject_unchanged(lambda: operations.execute(
            self.root, "test-task", self.artifact(), self.critical()))

    def test_execute_invalid_tokens_preserve_every_byte(self):
        task = self.task()
        for token in [None, True, False, -1, 1.0, "1", [], {}]:
            with self.subTest(token=token):
                self.reject_unchanged(lambda: operations.execute(
                    self.root, task["id"], self.artifact(), self.critical(),
                    expected_revision=token), "expected_revision")

    def test_stale_execute_cannot_replace_new_result_or_retry_identical(self):
        read_a = self.task()
        first = self.execute(read_a)
        replacement = self.artifact(); replacement["summary"] = "New presentation by B"
        second = self.execute(first, replacement)
        self.reject_unchanged(lambda: self.execute(read_a), "stale")
        self.reject_unchanged(lambda: self.execute(first, replacement), "stale")
        before = self.recorded_bytes()
        self.assertEqual(self.execute(second, replacement), second)
        self.assertEqual(self.recorded_bytes(), before)
        self.assertEqual(load(task_file(self.root, second["id"]))["output"], second["output"])

    def test_guard_precedes_version_block_and_candidate_rejection(self):
        stale = self.task()
        current = self.execute(stale)
        method = self.root / "standards/task-validation.md"
        method.write_bytes(method.read_bytes() + b"\nChanged method\n")
        bad = self.artifact(); bad["total"] = 6
        self.reject_unchanged(lambda: self.execute(stale, bad), "stale")
        with self.assertRaisesRegex(ValueError, "Pinned"):
            self.execute(current, bad)
        self.assertEqual(load(task_file(self.root, current["id"]))["status"], "blocked")

    def test_fresh_repair_after_block_and_owner_resume(self):
        task = self.task()
        bad = self.artifact(); bad["total"] = 6
        with self.assertRaisesRegex(Rejected, "incorrect total"):
            self.execute(task, bad)
        blocked = load(task_file(self.root, task["id"]))
        self.reject_unchanged(lambda: self.execute(task), "stale")
        repaired = self.execute(blocked)
        self.assertEqual(repaired["status"], "verified")
        method = self.root / "standards/task-validation.md"
        method.write_bytes(method.read_bytes() + b"\nChanged method\n")
        with self.assertRaisesRegex(ValueError, "Pinned"):
            self.execute(repaired)
        blocked = load(task_file(self.root, task["id"]))
        resumed = operations.decide(self.root, task["id"], blocked["revision"], "resume",
                                    "Owner reconciled method", "test-owner")
        self.assertEqual(self.execute(resumed)["status"], "verified")

    def test_observe_missing_invalid_revision_or_hash_preserves_every_byte(self):
        task = self.execute(self.task())
        base = (self.root, task["id"], "application", "Owner evidence", "test-owner")
        self.reject_unchanged(lambda: operations.observe(*base))
        for token in [None, True, False, -1, 1.0, "2", [], {}]:
            with self.subTest(revision=token):
                self.reject_unchanged(lambda: operations.observe(*base, expected_revision=token,
                    expected_result_sha256=task["output"]["sha256"]), "expected_revision")
        for token in [None, True, 42, "", "g" * 64, "a" * 63, "a" * 65,
                      "A" * 64, " " + task["output"]["sha256"], "0" * 64]:
            with self.subTest(sha256=token):
                self.reject_unchanged(lambda: operations.observe(*base,
                    expected_revision=task["revision"], expected_result_sha256=token), "result")

    def test_stale_observation_revision_and_hash_cannot_bind_new_result(self):
        first = self.execute(self.task())
        replacement = self.artifact(); replacement["summary"] = "Another exact result"
        second = self.execute(first, replacement)
        self.reject_unchanged(lambda: self.observe(first), "stale")
        self.reject_unchanged(lambda: operations.observe(self.root, second["id"], "effect",
            "Evidence for older result", "test-owner", expected_revision=second["revision"],
            expected_result_sha256=first["output"]["sha256"]), "stale")
        observed = self.observe(second, "effect", "Observed higher cost; negative outcome")
        self.assertEqual(observed["effect"]["result_sha256"], second["output"]["sha256"])
        self.assertEqual(observed["application"], {"status": "unknown"})
        self.assertEqual(observed["effect"]["evidence"], "Observed higher cost; negative outcome")
        repeated = self.observe(observed, "effect", "Observed higher cost; negative outcome")
        self.assertEqual(repeated["effect"]["status"], "observed")
        self.assertGreater(repeated["revision"], observed["revision"])

    def test_stale_tokens_checked_under_lock_before_validation(self):
        task = self.execute(self.task())
        with patch.object(operations, "load", wraps=operations.load) as loading:
            with patch.object(validation, "task", side_effect=AssertionError("too early")):
                self.reject_unchanged(lambda: operations.observe(self.root, task["id"],
                    "application", "Evidence", "test-owner", expected_revision=task["revision"] - 1,
                    expected_result_sha256=task["output"]["sha256"]), "stale")
            self.assertTrue(loading.called)

    def test_cancelled_unknown_and_unconfirmed_tasks_remain_protected(self):
        task = self.task(confirmed=False)
        self.reject_unchanged(lambda: self.execute(task), "unconfirmed")
        task = operations.decide(self.root, task["id"], task["revision"], "cancel",
                                 "Request withdrawn", "test-owner")
        self.reject_unchanged(lambda: self.execute(task), "cancelled")
        self.reject_unchanged(lambda: operations.observe(self.root, task["id"], "application",
            "Evidence", "test-owner", expected_revision=task["revision"],
            expected_result_sha256="0" * 64))
        unknown = operations.intake(self.root, "unknown-task", "Unknown definition", "test-owner",
                                   {"kind": "note"}, True, unknowns=["Meaning missing"])
        self.reject_unchanged(lambda: self.execute(unknown), "unconfirmed")

    def test_cli_requires_saved_revision_and_observation_hash(self):
        task = self.task()
        candidate = self.base / "candidate.json"; critical = self.base / "critical.json"
        write(candidate, self.artifact()); write(critical, self.critical())
        execute = ["execute", task["id"], "--artifact", str(candidate), "--critical", str(critical)]
        before = self.recorded_bytes()
        for extra in [[], ["--expected-revision", "invalid"], ["--expected-revision", "-1"],
                      ["--expected-revision", "0"]]:
            with self.subTest(execute=extra):
                result = self.command(*execute, *extra)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(self.recorded_bytes(), before)
        result = self.command(*execute, "--expected-revision", str(task["revision"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout)
        observe = ["observe", task["id"], "application", "--evidence", "Owner used result", "--actor", "test-owner"]
        before = self.recorded_bytes()
        for extra in [[], ["--expected-revision", str(task["revision"])],
                      ["--expected-result-sha256", task["output"]["sha256"]],
                      ["--expected-revision", "-1", "--expected-result-sha256", task["output"]["sha256"]],
                      ["--expected-revision", str(task["revision"]), "--expected-result-sha256", "bad"]]:
            with self.subTest(observe=extra):
                result = self.command(*observe, *extra)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(self.recorded_bytes(), before)
        result = self.command(*observe, "--expected-revision", str(task["revision"]),
                              "--expected-result-sha256", task["output"]["sha256"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["application"]["status"], "observed")
        before = self.recorded_bytes()
        result = self.command(*observe, "--expected-revision", str(task["revision"]),
                              "--expected-result-sha256", task["output"]["sha256"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.recorded_bytes(), before)

    def test_context_and_summary_show_independent_exact_result_layers(self):
        pending = self.execute(self.task())
        observed = self.observe(pending, "effect", "Owner saw worse outcome")
        context = operations.context(self.root, observed["id"])["tasks"][0]
        self.assertEqual(context["output"], observed["output"])
        entries = operations.summary(self.root)["entries"]
        entry = next(e for e in entries if e["id"] == observed["id"])
        for key in ["delivery", "application", "effect", "output"]:
            self.assertEqual(entry[key], observed[key])
        self.assertEqual(entry["delivery"]["status"], "pending")
        self.assertEqual(entry["application"]["status"], "unknown")
        self.assertEqual(entry["effect"]["status"], "observed")
        waiting = operations.intake(self.root, "waiting-task", "Await decision", "test-owner", {"kind": "note"})
        entry = next(e for e in operations.summary(self.root)["entries"] if e["id"] == waiting["id"])
        self.assertIsNone(entry["output"])
        self.assertEqual(entry["delivery"], {"status": "local"})

    def test_delivered_summary_does_not_infer_application_or_effect(self):
        task = self.execute(self.task())
        self.seed()
        remote = self.base / "summary-common.git"
        delivery.git(self.root, "clone", "--bare", str(self.root), str(remote))
        receipt = delivery.deliver(self.root, str(remote), task_id=task["id"])
        self.assertEqual(receipt["status"], "readback-confirmed")
        delivered = load(task_file(self.root, task["id"]))
        self.reject_unchanged(lambda: self.execute(task), "stale")
        entry = operations.summary(self.root)["entries"][0]
        self.assertEqual(entry["delivery"], delivered["delivery"])
        self.assertEqual(entry["delivery"]["status"], "delivered")
        self.assertEqual(entry["application"], {"status": "unknown"})
        self.assertEqual(entry["effect"], {"status": "unknown"})
        self.assertEqual(entry["output"], delivered["output"])
        owning = operations.intake(self.root, "delivery-summary", "Save delivery layers", "test-owner",
                                   {"kind": "action-summary"}, True)
        artifact = operations.summary(self.root, owning["id"])
        snapshot = load(self.root / artifact["sources"][0]["path"])
        self.assertEqual(snapshot["entries"][0], entry)

    def test_legacy_schema1_task_reads_but_unsafe_mutators_reject(self):
        task = self.task()
        task.pop("binding_scope")
        task["bindings"] = method_bindings(self.root, task["inputs"])
        write(task_file(self.root, task["id"]), task)
        self.assertEqual(operations.context(self.root, task["id"])["tasks"][0]["revision"], task["revision"])
        self.assertTrue(validation.task(self.root, task))
        self.reject_unchanged(lambda: operations.execute(self.root, task["id"], self.artifact(), self.critical()))
        verified = self.execute(task)
        self.assertEqual(verified["schema_version"], 1)
        self.assertNotIn("binding_scope", verified)
        self.reject_unchanged(lambda: operations.observe(self.root, task["id"], "application", "Owner evidence", "test-owner"))
        self.assertEqual(self.observe(verified)["application"]["status"], "observed")

    def test_saved_layers_and_legacy_summary_snapshots_remain_immutable(self):
        original = self.execute(self.task())
        summary_task = operations.intake(self.root, "summary-task", "Save actions", "test-owner",
                                         {"kind": "action-summary"}, True)
        artifact = operations.summary(self.root, summary_task["id"])
        snapshot_path = self.root / artifact["sources"][0]["path"]
        snapshot_bytes = snapshot_path.read_bytes()
        self.assertEqual(load(snapshot_path)["entries"], artifact["entries"])
        for key in ["delivery", "application", "effect", "output"]:
            self.assertEqual(artifact["entries"][0][key], original[key])
        critical = {"request_alignment": "Original actions retained", "counterexample": "Later state differs",
                    "limitations": ["Recorded snapshot"], "references": [artifact["sources"][0]["path"]]}
        verified = operations.execute(self.root, summary_task["id"], artifact, critical,
                                      expected_revision=summary_task["revision"])
        self.observe(original)
        self.assertEqual(snapshot_path.read_bytes(), snapshot_bytes)
        self.assertTrue(validation.task(self.root, verified))
        legacy = copy.deepcopy(artifact); snapshot = copy.deepcopy(load(snapshot_path))
        for entries in [legacy["entries"], snapshot["entries"]]:
            for entry in entries:
                for key in ["delivery", "application", "effect", "output"]:
                    entry.pop(key)
        relative = "work/summary-task/inputs/historical-schema1.json"
        write(self.root / relative, snapshot)
        legacy["sources"] = [{"path": relative, "sha256": digest(self.root / relative)}]
        historical_bytes = (self.root / relative).read_bytes()
        self.assertTrue(validation.result(self.root, legacy, summary_task["acceptance"]))
        old_task = operations.execute(self.root, summary_task["id"], legacy,
            {**critical, "references": [relative]}, expected_revision=verified["revision"])
        self.assertEqual(old_task["schema_version"], 1)
        self.assertTrue(validation.repository(self.root))
        self.assertEqual((self.root / relative).read_bytes(), historical_bytes)
        self.assertEqual(snapshot_path.read_bytes(), snapshot_bytes)
