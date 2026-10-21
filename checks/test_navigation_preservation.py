"""Current-route recovery and observed saving, on synthetic files only."""
from pathlib import Path
import unittest
from unittest.mock import patch

from checks.test_system import Fixture
from checks.fixture_helpers import execute_with_current_tokens_for_fixture
from core import Rejected, changed_bindings, config, load, task_file, write
import navigation
import operations
import preservation


class Navigation(Fixture):
    def test_organization_reports_remain_outside_the_consumer_set(self):
        import organization
        from core import digest
        target = self.root / "company/projects/note.md"
        target.write_text("Before\n")
        selected = ["company/projects/note.md"]
        initial = organization.inspect(self.root, selected, "route-repair")
        evidence = self.root / "work/route-repair/inputs/organization-snapshot.json"
        write(evidence, initial)
        task = operations.intake(self.root, "route-repair", "Repair the current note route", "test-owner",
                                 {"kind": "note", "target": selected[0]}, True,
                                 inputs=[evidence.relative_to(self.root).as_posix()], skill="company-organization")
        prewrite = organization.inspect(self.root, selected, "route-repair")
        self.assertTrue(all(prewrite["preimages"].get(k) == v for k, v in initial["preimages"].items()))
        self.assertEqual(set(prewrite["preimages"]) - set(initial["preimages"]), {"work/route-repair/task.json"})
        self.assertEqual(task["bindings"][evidence.relative_to(self.root).as_posix()], digest(evidence))
        proof = self.root / ".system/organization/route-repair"
        write(proof / "prewrite.json", prewrite)
        expected = dict(prewrite["preimages"])
        import hashlib
        expected[selected[0]] = hashlib.sha256(b"After\n").hexdigest()
        write(proof / "change-set.json", expected)
        organization.compare(self.root, prewrite)
        target.write_bytes(b"After\n")
        observed = organization.inspect(self.root, selected, "route-repair")
        self.assertEqual(observed["preimages"], expected)
        write(proof / "postimage.json", observed)
        organization.compare(self.root, load(proof / "postimage.json"))
        self.assertEqual(digest(evidence), task["bindings"][evidence.relative_to(self.root).as_posix()])
        target.write_text("Concurrent change\n")
        with self.assertRaises(Rejected): organization.compare(self.root, observed)

    def configure_routes(self):
        current = self.root / "company/current-plan.md"
        current.write_text("# Current owner\nNext: resume the accepted task.\n", encoding="utf-8")
        cfg = config(self.root)
        cfg["workspace"] = {"state_owners": ["company/old-plan.md"], "navigation": {
            "redirects": {"company/old-plan.md": "company/current-plan.md"},
            "consumers": ["company/config.yaml", "README.md"]}}
        write(self.root / "company/config.yaml", cfg)
        readme = self.root / "README.md"
        readme.write_text(readme.read_text() + "\n[Plan](company/old-plan.md)\n", encoding="utf-8")
        return cfg

    def test_current_routes_recover_and_a_new_process_finds_owner(self):
        cfg = self.configure_routes()
        before = navigation.check(self.root)
        self.assertEqual(len(before["stale"]), 2)
        self.assertTrue(before["owners"][0]["available"])
        self.assertEqual(before["owners"][0]["canonical"], "company/current-plan.md")
        with self.assertRaises(Rejected): navigation.validate(self.root)
        # The agent repairs proved active consumers; no silent file relocation.
        cfg["workspace"]["state_owners"] = ["company/current-plan.md"]
        cfg["decisions"] = [{"id": "past-location", "source": "company/old-plan.md"}]
        write(self.root / "company/config.yaml", cfg)
        readme = self.root / "README.md"
        readme.write_text(readme.read_text().replace("(company/old-plan.md)", "(company/current-plan.md)"))
        self.assertTrue(navigation.validate(self.root))
        current = self.cli("context")["navigation"]
        self.assertFalse(current["stale"])
        self.assertEqual(current["owners"][0]["canonical"], "company/current-plan.md")
        self.assertEqual(navigation.check(self.root), navigation.check(self.root))

    def test_historical_paths_remain_byte_exact(self):
        self.configure_routes()
        history = self.root / "work/previous/task.json"
        write(history, {"input": "company/old-plan.md", "sha256": "historical-bytes"})
        original = history.read_bytes()
        observed = navigation.check(self.root, ["work/previous/task.json"])
        self.assertEqual(observed["skipped_historical"], ["work/previous/task.json"])
        self.assertEqual(history.read_bytes(), original)

    def test_cycles_missing_targets_and_unsafe_paths_are_explicit(self):
        cfg = self.configure_routes()
        cfg["workspace"]["navigation"]["redirects"]["company/current-plan.md"] = "company/old-plan.md"
        write(self.root / "company/config.yaml", cfg)
        with self.assertRaisesRegex(Rejected, "cycle"): navigation.check(self.root)
        cfg["workspace"]["navigation"]["redirects"] = {"company/old-plan.md": "company/missing.md"}
        write(self.root / "company/config.yaml", cfg)
        self.assertFalse(navigation.resolve(self.root, "company/old-plan.md")["available"])
        self.assertTrue(navigation.check(self.root)["issues"])
        cfg["workspace"]["navigation"]["redirects"] = {"../escape.md": "company/current-plan.md"}
        write(self.root / "company/config.yaml", cfg)
        with self.assertRaises(Rejected): navigation.check(self.root)

    def test_route_aliases_and_case_collisions_are_rejected(self):
        cfg = self.configure_routes()
        (self.root / "company/alias.md").symlink_to("current-plan.md")
        cfg["workspace"]["navigation"]["redirects"] = {"company/old-plan.md": "company/alias.md"}
        write(self.root / "company/config.yaml", cfg)
        with self.assertRaises(Rejected): navigation.check(self.root)
        cfg["workspace"]["navigation"]["redirects"] = {"company/Old.md": "company/current-plan.md", "company/old.md": "company/current-plan.md"}
        write(self.root / "company/config.yaml", cfg)
        with self.assertRaises(ValueError): navigation.check(self.root)

    def test_missing_local_only_route_is_visible_and_not_a_freshness_claim(self):
        cfg = self.configure_routes()
        cfg["workspace"]["navigation"].update(redirects={"company/old-plan.md": "private/plan.md"}, local_only=["private/plan.md"], consumers=[])
        write(self.root / "company/config.yaml", cfg)
        result = navigation.check(self.root)
        self.assertFalse(result["owners"][0]["available"])
        self.assertTrue(result["owners"][0]["local_only"])
        self.assertFalse(result["issues"])

    def test_navigation_configuration_and_current_consumers_are_pinned(self):
        cfg = self.configure_routes()
        task = operations.intake(self.root, "route-dependency", "Inspect current owner", "test-owner",
                                 {"kind": "text"}, True, skill="company-context")
        self.assertIn("scripts/navigation.py", task["binding_scope"]["paths"])
        self.assertIn("README.md", task["binding_scope"]["paths"])
        cfg["workspace"]["navigation"]["redirects"]["company/another-old.md"] = "company/current-plan.md"
        write(self.root / "company/config.yaml", cfg)
        self.assertIn("config:navigation", changed_bindings(self.root, task["bindings"], task["binding_scope"]))


class Preservation(Fixture):
    def test_nested_company_without_own_git_never_probes_parent_repository(self):
        import delivery
        self.seed(self.base)
        (self.root / "notes.md").write_text("Local document in a parent repository\n")
        with patch.object(delivery, "git", side_effect=AssertionError("must not inspect parent Git")):
            observed = preservation.inspect(self.root, ["notes.md"])["files"][0]
        self.assertTrue(observed["local"]["present"])
        self.assertIsNone(observed["git"]["head"])
        self.assertIsNone(observed["git"]["exact_bytes_in_head"])
        self.assertEqual(observed["delivery"]["status"], "unknown")

    def test_disk_git_and_recorded_delivery_are_different_observations(self):
        file = self.root / "notes.md"
        file.write_text("Local author notes\n")
        selected = ["notes.md"]
        before = preservation.inspect(self.root, selected)["files"][0]
        self.assertTrue(before["local"]["present"])
        self.assertIsNone(before["git"]["exact_bytes_in_head"])
        self.seed()
        saved = preservation.inspect(self.root, selected)["files"][0]
        self.assertTrue(saved["git"]["exact_bytes_in_head"])
        self.assertEqual(saved["delivery"]["status"], "unknown")
        file.write_text("New local bytes\n")
        self.assertFalse(preservation.inspect(self.root, selected)["files"][0]["git"]["exact_bytes_in_head"])

    def test_context_keeps_recorded_layers_separate_from_current_output(self):
        self.task()
        t = execute_with_current_tokens_for_fixture(self.root, "test-task", self.artifact(), self.critical())
        t["delivery"] = {"status": "delivered", "readback": True, "commit": "synthetic-record-only", "result_sha256": t["output"]["sha256"]}
        t["application"] = {"status": "unknown"}
        write(task_file(self.root, "test-task"), t)
        context = operations.context(self.root, "test-task")["tasks"][0]
        self.assertEqual(context["delivery"]["status"], "delivered")
        self.assertTrue(context["output_local"]["matches_recorded"])
        current = preservation.inspect(self.root, [t["output"]["path"]], "test-task")["files"][0]
        self.assertEqual(current["delivery"]["status"], "recorded-readback")
        self.assertFalse(current["delivery"]["fresh_remote_check"])
        t["delivery"]["readback"] = False
        write(task_file(self.root, "test-task"), t)
        self.assertEqual(preservation.inspect(self.root, [t["output"]["path"]], "test-task")["files"][0]["delivery"]["status"], "recorded-delivery-incomplete")
        (self.root / t["output"]["path"]).write_text("Changed after recorded delivery")
        self.assertFalse(operations.context(self.root, "test-task")["tasks"][0]["output_local"]["matches_recorded"])
        self.assertEqual(preservation.inspect(self.root, [t["output"]["path"]], "test-task")["files"][0]["delivery"]["status"], "current-bytes-not-acknowledged")

    def snapshot_source(self):
        source = self.base / "private-documents"
        source.mkdir()
        (source / "report.md").write_bytes(b"Author notes\r\nExact raw bytes\r\n")
        (source / "nested").mkdir()
        (source / "nested/decision.json").write_text('{"decision":"test only"}\n')
        return source, ["report.md", "nested/decision.json"]

    def test_selected_private_documents_restore_exactly_without_git(self):
        source, selected = self.snapshot_source()
        copied = preservation.snapshot(source, selected, self.base / "selected-backup")
        restored = preservation.restore(copied["path"], self.base / "restored-documents")
        self.assertTrue(restored["verified"])
        for relative in selected:
            self.assertEqual((source / relative).read_bytes(), (Path(restored["path"]) / relative).read_bytes())
        self.assertFalse((Path(restored["path"]) / ".git").exists())
        with self.assertRaises(Rejected): preservation.snapshot(source, selected, copied["path"])
        with self.assertRaises(Rejected): preservation.restore(copied["path"], restored["path"])

    def test_corrupt_extra_and_traversing_payloads_fail_before_restore_writes(self):
        for attack in ("corrupt", "extra", "traversal"):
            source = self.base / ("source-" + attack)
            source.mkdir()
            (source / "report.md").write_text("Original\n")
            backup = self.base / ("backup-" + attack)
            preservation.snapshot(source, ["report.md"], backup)
            if attack == "corrupt": (backup / "files/report.md").write_text("Tampered\n")
            if attack == "extra": (backup / "files/extra.md").write_text("Unexpected\n")
            if attack == "traversal":
                manifest = load(backup / "manifest.json")
                manifest["files"]["../escape.md"] = manifest["files"].pop("report.md")
                write(backup / "manifest.json", manifest)
            restored = self.base / ("restore-" + attack)
            with self.assertRaises(Rejected): preservation.restore(backup, restored)
            self.assertFalse(restored.exists())

    def test_snapshot_rejects_aliases_sensitive_names_and_oversized_selection(self):
        source, selected = self.snapshot_source()
        (source / "alias.md").symlink_to("report.md")
        with self.assertRaises(Rejected): preservation.snapshot(source, ["alias.md"], self.base / "alias-copy")
        (source / "credentials.json").write_text('{"example":"synthetic filename test"}')
        with self.assertRaises(Rejected): preservation.snapshot(source, ["credentials.json"], self.base / "private-copy")
        with patch.object(preservation, "MAX_TOTAL_BYTES", 1):
            with self.assertRaises(Rejected): preservation.snapshot(source, selected, self.base / "oversize-copy")
        with self.assertRaises(Rejected): preservation.snapshot(source, ["../escape.md"], self.base / "escaping-copy")

    def test_source_edit_aborts_snapshot_without_a_complete_manifest(self):
        source, selected = self.snapshot_source()
        original_copy = preservation._copy
        def changed(source_file, target, initial, expected_hash=None):
            source_file.write_text("Concurrent author edit\n")
            return original_copy(source_file, target, initial, expected_hash)
        destination = self.base / "interrupted-snapshot"
        with patch.object(preservation, "_copy", side_effect=changed):
            with self.assertRaisesRegex(Rejected, "changed before copy"):
                preservation.snapshot(source, selected, destination)
        self.assertFalse((destination / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
