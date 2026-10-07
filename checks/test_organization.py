"""Read-only repository organization evidence and task-safe change contracts."""
from __future__ import annotations
import json
import subprocess
import sys
import unittest

from checks.test_system import Fixture
from core import Rejected, config, digest, load, task_file, write
import dependencies
import operations
import organization
import validation


class Organization(Fixture):
    def item(self, result, relative):
        return next(item for item in result["items"] if item["path"] == relative)

    def test_known_entity_map_links_and_ordinary_document_stay_distinct(self):
        standard = self.root / "standards/new-standard.md"
        standard.write_text(
            "---\nid: new-standard\nversion: 1\nowner: test-owner\n---\n"
            "# New standard\n\n[Broken](missing.md)\n",
            encoding="utf-8", newline="\n")
        note = self.root / "company/projects/ordinary-note.md"
        note.write_text("# Ordinary note\n", encoding="utf-8", newline="\n")
        result = organization.inspect(
            self.root, ["standards/new-standard.md", "company/projects/ordinary-note.md"])

        entity = self.item(result, "standards/new-standard.md")
        self.assertEqual(entity["kind"], "standard")
        self.assertEqual(entity["placement"], "canonical")
        self.assertEqual(entity["required_map"], "standards/README.md")
        self.assertFalse(entity["mapped"])
        self.assertEqual(entity["broken_links"][0]["target"], "missing.md")

        ordinary = self.item(result, "company/projects/ordinary-note.md")
        self.assertEqual(ordinary["kind"], "document")
        self.assertEqual(ordinary["placement"], "semantic-required")
        self.assertIsNone(ordinary["required_map"])
        self.assertIsNone(ordinary["canonical_destination"])
        self.assertTrue(result["read_only"])

    def test_missing_target_reports_markdown_structured_and_immutable_consumers(self):
        target = "company/projects/old-note.md"
        consumer = self.root / "company/projects/consumer.md"
        consumer.write_text("[Old](old-note.md)\n", encoding="utf-8", newline="\n")
        second_consumer = self.root / "company/operations/consumer.md"
        second_consumer.write_text("[Old](../projects/old-note.md)\n",
                                   encoding="utf-8", newline="\n")
        structured = self.root / "company/projects/consumer.yaml"
        write(structured, {"source": target, "nested": {target: "kept"}})
        pinned = self.root / "work/historical-task/task.json"
        write(pinned, {"inputs": [target], "bindings": {target: "0" * 64},
                       "history": [{"output": {"path": target, "sha256": "1" * 64}}]})

        result = organization.inspect(self.root, [target], task_id="organization-task")
        item = self.item(result, target)
        self.assertEqual(item["state"], "absent")
        self.assertEqual([value["path"] for value in item["inbound_links"]],
                         ["company/operations/consumer.md",
                          "company/projects/consumer.md"])
        self.assertEqual(
            {value["path"] for value in item["structured_consumers"]},
            {"company/projects/consumer.yaml", "work/historical-task/task.json"})
        self.assertEqual([value["path"] for value in item["immutable_pins"]],
                         ["work/historical-task/task.json"])
        self.assertEqual(item["recommended_action"], "leave-open")

    def test_aliases_bad_paths_duplicates_and_outside_manifest_never_read(self):
        outside = self.base / "outside-secret.md"
        outside.write_text("must-not-be-read-marker", encoding="utf-8", newline="\n")
        alias = self.root / "company/projects/foreign.md"
        alias.symlink_to(outside)
        note = self.root / "company/projects/note.md"
        note.write_text("Safe\n", encoding="utf-8", newline="\n")

        result = organization.inspect(self.root, ["company/projects/note.md"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["skipped_aliases"],
                         ["company/projects/foreign.md"])
        self.assertNotIn("must-not-be-read-marker", json.dumps(result))

        for values in [[], ["../outside.md"], [str(outside)],
                       ["company/projects/note.md", "company/projects/note.md"],
                       ["company/projects/foreign.md"]]:
            with self.subTest(values=values), self.assertRaises(Rejected):
                organization.inspect(self.root, values)

    def test_snapshot_compare_detects_drift_and_inspection_writes_nothing(self):
        note = self.root / "company/projects/note.md"
        note.write_text("Before\n", encoding="utf-8", newline="\n")
        before = {file.relative_to(self.root).as_posix(): digest(file)
                  for file in self.root.rglob("*") if file.is_file() and not file.is_symlink()}
        snapshot = organization.inspect(self.root, ["company/projects/note.md"],
                                        task_id="organization-task")
        organization.compare(self.root, snapshot)
        after = {file.relative_to(self.root).as_posix(): digest(file)
                 for file in self.root.rglob("*") if file.is_file() and not file.is_symlink()}
        self.assertEqual(before, after)

        note.write_text("Concurrent change\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(Rejected, "preimage changed"):
            organization.compare(self.root, snapshot)
        postimage = organization.inspect(self.root, ["company/projects/note.md"],
                                         task_id="organization-task")
        self.assertEqual(organization.compare(self.root, postimage)["status"], "unchanged")

        alias = self.root / "company/operations/alias-projects"
        alias.symlink_to(self.root / "company/projects", target_is_directory=True)
        with self.assertRaisesRegex(Rejected, "inspection coverage changed"):
            organization.compare(self.root, postimage)

    def test_cli_reads_only_in_company_manifests_and_checks_expected_postimage(self):
        note = self.root / "company/projects/cli-note.md"
        note.write_text("CLI\n", encoding="utf-8", newline="\n")
        paths_file = self.root / "work/organization-cli/inputs/paths.json"
        write(paths_file, ["company/projects/cli-note.md"])
        command = [sys.executable, str(self.root / "scripts/system.py"), "--root",
                   str(self.root), "organization-check", "--paths-file",
                   str(paths_file), "--task", "organization-cli"]
        first = subprocess.run(command, check=True, capture_output=True, text=True)
        snapshot = json.loads(first.stdout)
        self.assertEqual(snapshot["status"], "inspection")
        expected = self.root / "work/organization-cli/inputs/organization-snapshot.json"
        write(expected, snapshot)
        second = subprocess.run(command + ["--expect", str(expected)], check=True,
                                capture_output=True, text=True)
        self.assertEqual(json.loads(second.stdout)["status"], "unchanged")

        outside = self.base / "outside-paths.json"
        write(outside, ["company/projects/cli-note.md"])
        outside_command = list(command)
        outside_command[6] = str(outside)
        rejected = subprocess.run(outside_command, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("outside company", rejected.stderr)

        alias_manifest = self.root / "work/organization-cli/inputs/alias.json"
        alias_manifest.symlink_to(outside)
        alias_command = list(command)
        alias_command[6] = str(alias_manifest)
        rejected = subprocess.run(alias_command, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertRegex(rejected.stderr, "outside company|symlink/junction")

    def test_own_snapshot_allows_verified_change_and_selected_dependencies(self):
        target = self.root / "company/projects/organized-note.md"
        target.write_text("Draft\n", encoding="utf-8", newline="\n")
        paths = ["company/projects/organized-note.md", "company/projects/README.md"]
        snapshot = organization.inspect(self.root, paths, task_id="organization-task")
        snapshot_file = self.root / "work/organization-task/inputs/organization-snapshot.json"
        write(snapshot_file, snapshot)
        snapshot_hash = digest(snapshot_file)

        local = self.root / "company/standards/organization-rule.md"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(
            "---\nid: organization-rule\nversion: 1\nowner: test-owner\n---\n"
            "# Local organization rule\n",
            encoding="utf-8", newline="\n")
        cfg = config(self.root)
        cfg["bindings"]["organization"] = {
            "base": "standards/system-evolution.md",
            "base_sha256": digest(self.root / "standards/system-evolution.md"),
            "local": "company/standards/organization-rule.md",
            "approved_by": "test-owner", "reason": "Synthetic accepted placement rule",
            "checks": "organization fixture", "dependency_paths": [],
        }
        write(self.root / "company/config.yaml", cfg)
        task = operations.intake(
            self.root, "organization-task", "Organize one synthetic note", "test-owner",
            {"kind": "note"}, True,
            inputs=["work/organization-task/inputs/organization-snapshot.json"],
            skill="company-organization")
        scope_paths = set(task["binding_scope"]["paths"])
        self.assertIn("scripts/organization.py", scope_paths)
        self.assertIn("company/standards/organization-rule.md", scope_paths)
        self.assertNotIn("company/projects/organized-note.md", scope_paths)

        unrelated = self.root / "company/marketing/README.md"
        unrelated.write_text(unrelated.read_text(encoding="utf-8") + "\nUnrelated.\n",
                             encoding="utf-8", newline="\n")
        self.assertEqual(dependencies.changed(self.root, task["bindings"],
                                              task["binding_scope"]), [])

        target.write_text("Organized\n", encoding="utf-8", newline="\n")
        project_map = self.root / "company/projects/README.md"
        project_map.write_text(project_map.read_text(encoding="utf-8") +
                               "\n[Organized note](organized-note.md)\n",
                               encoding="utf-8", newline="\n")
        postimage = organization.inspect(self.root, paths, task_id="organization-task")
        organization.compare(self.root, postimage)
        artifact = {
            "schema_version": 1, "company_id": "test-company", "kind": "note",
            "summary": "Synthetic note has one owner and one map route",
            "next_action": "Recipient can use the canonical project map",
            "limitations": ["Synthetic local organization; native discovery unknown"],
            "sources": [{"path": snapshot_file.relative_to(self.root).as_posix(),
                         "sha256": snapshot_hash}],
        }
        critical = {
            "request_alignment": "One note and its existing map were organized",
            "counterexample": "A second status document would violate the owner rule",
            "limitations": ["Synthetic fixture"],
            "references": [snapshot_file.relative_to(self.root).as_posix()],
        }
        verified = operations.execute(self.root, "organization-task", artifact, critical)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(digest(snapshot_file), snapshot_hash)
        self.assertEqual(self.item(postimage, "company/projects/organized-note.md")["state"], "file")

        local.write_text(local.read_text(encoding="utf-8") + "\nChanged.\n",
                         encoding="utf-8", newline="\n")
        self.assertIn("company/standards/organization-rule.md",
                      dependencies.changed(self.root, verified["bindings"],
                                           verified["binding_scope"]))

    def test_verified_and_delivered_task_pins_block_reorganization(self):
        task = self.task("pinned-task")
        verified = operations.execute(self.root, "pinned-task", self.artifact(), self.critical())
        verified["delivery"] = {
            "status": "delivered", "result_sha256": verified["output"]["sha256"],
            "commit": "synthetic-commit", "readback": True,
        }
        write(task_file(self.root, "pinned-task"), verified)
        validation.task(self.root, verified)

        result = organization.inspect(self.root, ["company/sources/input.json"],
                                      task_id="organization-task")
        item = self.item(result, "company/sources/input.json")
        self.assertTrue(item["immutable_pins"])
        self.assertEqual(item["recommended_action"], "leave-open")


if __name__ == "__main__":
    unittest.main()
