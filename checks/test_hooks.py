"""Bounded shared-code/protocol fixtures; these do not prove native host coverage."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "template/scripts"
sys.path.insert(0, str(SCRIPTS))
import hooks
from core import Rejected, config, digest, load, write


class Hooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="company-hooks-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "company with spaces $literal"
        shutil.copytree(SCRIPTS.parent, self.root, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        from checks.fixture_helpers import directory_projections
        directory_projections(self.root)
        cfg = config(self.root)
        cfg.update(id="test-company", owner="test-owner", name="Synthetic hooks fixture")
        cfg["permissions"]["local_work"] = True
        cfg["hooks"] = {"enabled": False, "disabled": []}
        write(self.root / "company/config.yaml", cfg)

    def enable(self):
        cfg = config(self.root); cfg["hooks"]["enabled"] = True
        write(self.root / "company/config.yaml", cfg)

    def task(self, revision=0):
        task = {"id": "test-task", "company_id": "test-company", "revision": revision, "status": "active", "next_action": "Read current input", "evidence": None, "errors": [], "bindings": {"standards/task-validation.md": digest(self.root / "standards/task-validation.md")}}
        write(self.root / "work/test-task/task.json", task)
        return task

    def write_payload(self, relative, content):
        return {"cwd": str(self.root), "tool_name": "Write", "tool_input": {"file_path": relative, "content": content}}

    def native(self, harness, event, payload, task=None, expected=None):
        args = [sys.executable, str(self.root / "scripts/hooks.py"), "--root", str(self.root), "--namespace", hooks.NAMESPACE, "--harness", harness, "--event", event, "--definition-hash", expected or hooks.definition_hash(self.root, harness)]
        if task: args += ["--task", task]
        return subprocess.run(args, input=json.dumps(payload), text=True, capture_output=True, cwd=self.root / "company", timeout=8)

    def standard(self, entity="new-standard"):
        return f"---\nid: {entity}\nversion: 1\nowner: test-owner\n---\n# Test standard\n"

    def test_manifest_exact_six_schema_and_no_yaml_commands(self):
        self.assertEqual({item["id"] for item in hooks.manifest(self.root)["hooks"]}, hooks.SCENARIOS)
        file = self.root / "hooks/manifest.yaml"; value = load(file)
        value["hooks"][0]["command"] = "echo forbidden"
        write(file, value)
        with self.assertRaisesRegex(Rejected, "fields"): hooks.manifest(self.root)

    def test_payload_normalization_independent_of_method_dependencies(self):
        payload = self.write_payload("company/projects/test.md", "literal $(do-not-run)")
        self.assertEqual(hooks._changes(self.root, payload), [{"path": "company/projects/test.md", "content": "literal $(do-not-run)"}])
        payload["cwd"] = str(self.root / "company")
        payload["tool_input"]["file_path"] = "projects/test.md"
        self.assertEqual(hooks._changes(self.root, payload)[0]["path"], "company/projects/test.md")
        file = self.root / "company/projects/test.md"; file.write_text("old once", encoding="utf-8", newline="\n")
        value = {"tool_name": "Edit", "tool_input": {"file_path": str(file), "old_string": "old", "new_string": "new"}}
        self.assertEqual(hooks._changes(self.root, value)[0]["content"], "new once")
        patch = {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Add File: standards/new-standard.md\n+# Literal\n*** End Patch"}}
        self.assertEqual(hooks._changes(self.root, patch)[0]["content"], "# Literal\n")
        for relative in ["../outside.md", str(self.root.parent / "outside.md")]:
            with self.subTest(relative=relative), self.assertRaises(Rejected): hooks._changes(self.root, self.write_payload(relative, "bad"))

    def test_publication_hint_recognizes_fixed_cli_without_execution(self):
        import shlex
        fixed = shlex.join([sys.executable, str(self.root / "scripts/system.py"), "deliver", "remote"])
        payload = {"tool_name": "Bash", "cwd": str(self.root / "company"), "tool_input": {"command": fixed}}
        self.assertTrue(hooks._publication_invocation(self.root, payload))
        for command in ["git push", "echo scripts/system.py deliver", "python3 /tmp/scripts/system.py deliver", "malformed 'quote"]:
            payload["tool_input"]["command"] = command
            self.assertFalse(hooks._publication_invocation(self.root, payload))

    def test_manifest_invalid_event_and_timeout(self):
        file = self.root / "hooks/manifest.yaml"; initial = load(file)
        for field, value in [("events", ["PermissionRequest"]), ("timeout", 4), ("handler", "shell")]:
            candidate = copy.deepcopy(initial); candidate["hooks"][0][field] = value; write(file, candidate)
            with self.subTest(field=field), self.assertRaises(Rejected): hooks.manifest(self.root)

    def test_disabled_native_and_working_common_fallback(self):
        result = hooks.dispatch(self.root, "codex", "SessionStart", {}, native=True)
        self.assertEqual(result["status"], "disabled")
        result = hooks.dispatch(self.root, "common", "SessionStart", {})
        self.assertEqual(result["status"], "advisory")
        self.assertIn("company/README.md", result["messages"][0])
        self.assertFalse((self.root / ".codex/hooks.json").exists())

    def test_untrusted_or_missing_expected_definition_never_passes(self):
        self.enable()
        result = hooks.dispatch(self.root, "codex", "SessionStart", {"hook_trusted": False}, native=True)
        self.assertEqual(result["status"], "untrusted")
        with self.assertRaisesRegex(Rejected, "fingerprint required"):
            hooks.dispatch(self.root, "codex", "SessionStart", {}, native=True)

    def test_default_project_is_preview_without_activation_or_files(self):
        result = hooks.project(self.root, "codex")
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["enabled"])
        self.assertFalse(result["changed"])
        self.assertFalse((self.root / ".system/cache").exists())
        hooks.project(self.root, "codex", apply=True)
        self.assertFalse((self.root / ".codex/hooks.json").exists())

    def test_wrong_place_and_duplicate_proved_denial_ordinary_note_allowed(self):
        content = (self.root / "skills/company-context/SKILL.md").read_text(encoding="utf-8")
        for relative in ["work/misplaced/SKILL.md", "skills/duplicate/SKILL.md"]:
            with self.subTest(relative=relative):
                result = hooks.dispatch(self.root, "common", "PreToolUse", self.write_payload(relative, content))
                self.assertEqual(result["status"], "proved-violation", result)
                output = hooks.native_output("PreToolUse", result)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        result = hooks.dispatch(self.root, "common", "PreToolUse", self.write_payload("company/projects/new-note.md", "Business note"))
        self.assertNotEqual(result["status"], "proved-violation")

    def test_add_patch_and_edit_use_shared_validator(self):
        payload = {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Add File: standards/not-the-id.md\n" + "\n".join("+" + line for line in self.standard().splitlines()) + "\n*** End Patch"}}
        self.assertEqual(hooks.dispatch(self.root, "common", "PreToolUse", payload)["status"], "proved-violation")
        relative = "standards/new-standard.md"; (self.root / relative).write_text(self.standard(), encoding="utf-8", newline="\n")
        payload = {"tool_name": "Edit", "tool_input": {"file_path": relative, "old_string": "new-standard", "new_string": "wrong-id"}}
        self.assertEqual(hooks.dispatch(self.root, "common", "PreToolUse", payload)["status"], "proved-violation")

    def test_update_patch_partial_metadata_is_advisory_not_denial(self):
        payload = {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: standards/task-validation.md\n@@\n-old\n+new\n*** End Patch"}}
        self.assertNotEqual(hooks.dispatch(self.root, "common", "PreToolUse", payload)["status"], "proved-violation")

    def test_post_write_missing_map_advisory_and_linked_positive(self):
        relative = "standards/new-standard.md"; content = self.standard()
        (self.root / relative).write_text(content, encoding="utf-8", newline="\n")
        result = hooks.dispatch(self.root, "common", "PostToolUse", self.write_payload(relative, content))
        self.assertEqual(result["status"], "advisory", result)
        self.assertIn("map", " ".join(result["messages"]).lower())
        index = self.root / "standards/README.md"; index.write_text(index.read_text(encoding="utf-8") + "\n[New](new-standard.md)\n", encoding="utf-8", newline="\n")
        result = hooks.dispatch(self.root, "common", "PostToolUse", self.write_payload(relative, content))
        self.assertNotIn("map", " ".join(result["messages"]).lower())
        self.assertNotIn("permissionDecision", json.dumps(hooks.native_output("PostToolUse", result)))

    def test_post_write_reads_actual_bytes_instead_of_proposed_payload(self):
        relative = "standards/new-standard.md"; file = self.root / relative
        file.write_text("Actual incomplete write without frontmatter", encoding="utf-8", newline="\n")
        result = hooks.dispatch(self.root, "common", "PostToolUse", self.write_payload(relative, self.standard()))
        self.assertIn("missing frontmatter", " ".join(result["messages"]))
        self.assertEqual(result["status"], "advisory")

    def test_bad_payload_event_root_and_paths_rejected(self):
        for payload in [[], {"tool_name": "Write", "tool_input": "bad"}, {"cwd": str(self.root.parent)}, {"hook_event_name": "Stop"}, self.write_payload("../outside.md", "bad"), self.write_payload(str(self.root.parent / "outside.md"), "bad"), {"tool_name": "apply_patch", "tool_input": {"command": "bash -c 'write anything'"}}]:
            with self.subTest(payload=payload), self.assertRaises(Rejected): hooks.dispatch(self.root, "common", "PreToolUse", payload)
        with self.assertRaises(Rejected): hooks.dispatch(self.root, "common", "PreToolUse", {"irrelevant": "x" * hooks.MAX_INPUT})
        with self.assertRaises(Rejected): hooks.dispatch(self.root.parent, "common", "SessionStart", {})
        with self.assertRaises(Rejected): hooks.dispatch(Path("relative"), "common", "SessionStart", {})

    def test_subdirectory_absolute_quoted_projection_executes(self):
        self.enable()
        result = hooks.project(self.root, "codex", apply=True, enabled=True)
        command = result["projection"][0]["group"]["hooks"][0]["command"]
        import shlex
        process = subprocess.run(shlex.split(command), input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(self.root / "company")}), text=True, capture_output=True, cwd=self.root / "company", timeout=8)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("additionalContext", json.loads(process.stdout)["hookSpecificOutput"])

    def test_other_settings_preserved_idempotent_and_owned_collision(self):
        for harness, relative in hooks.TARGETS.items():
            with self.subTest(harness=harness):
                foreign = {"type": "command", "command": "echo foreign", "timeout": 2}
                original = {"custom": {"owner": "foreign"}, "hooks": {"SessionStart": [{"matcher": "resume", "hooks": [foreign]}]}}
                write(self.root / relative, original)
                result = hooks.project(self.root, harness, apply=True, enabled=True)
                self.assertEqual(result["status"], "projected")
                projected = load(self.root / relative)
                self.assertEqual(projected["custom"], original["custom"])
                self.assertEqual(projected["hooks"]["SessionStart"][0], original["hooks"]["SessionStart"][0])
                before = (self.root / relative).read_bytes()
                self.assertEqual(hooks.project(self.root, harness, apply=True, enabled=True)["status"], "unchanged")
                self.assertEqual((self.root / relative).read_bytes(), before)
                projected["hooks"]["SessionStart"][1]["matcher"] = "local override"; write(self.root / relative, projected)
                before = (self.root / relative).read_bytes()
                self.assertEqual(hooks.project(self.root, harness, apply=True, enabled=True)["status"], "conflict")
                self.assertEqual((self.root / relative).read_bytes(), before)

    def test_missing_receipt_unknown_namespace_and_mixed_group_conflict(self):
        hooks.project(self.root, "codex", apply=True, enabled=True)
        receipt = self.root / ".system/hooks-project-codex.json"; receipt.unlink()
        file = self.root / ".codex/hooks.json"; before = file.read_bytes()
        self.assertEqual(hooks.project(self.root, "codex", apply=True, enabled=True)["status"], "conflict")
        self.assertEqual(file.read_bytes(), before)
        file.unlink()
        hooks.project(self.root, "codex", apply=True, enabled=True)
        value = load(file); value["hooks"]["SessionStart"][0]["hooks"].append({"type": "command", "command": "echo another-owner"}); write(file, value)
        self.assertEqual(hooks.project(self.root, "codex", apply=True, enabled=True)["status"], "conflict")

    def test_legacy_proof_migration_exact_and_new_proof_precedence(self):
        hooks.project(self.root, "codex", apply=True, enabled=True)
        proof = self.root / ".system/hooks-project-codex.json"
        legacy = self.root / ".system/cache/hooks-project-codex.json"
        legacy.parent.mkdir(parents=True, exist_ok=True); shutil.move(proof, legacy)
        old = legacy.read_bytes()
        result = hooks.project(self.root, "codex", apply=True, enabled=True)
        self.assertTrue(result["legacy_migration"])
        self.assertEqual(proof.read_bytes(), old)
        self.assertEqual(legacy.read_bytes(), old)
        legacy.write_text("{malformed ignored legacy", encoding="utf-8", newline="\n")
        original_load = hooks.bounded_load
        def forbid_legacy(file):
            self.assertNotEqual(file, legacy, "new proof must prevent legacy reads")
            return original_load(file)
        with patch.object(hooks, "bounded_load", side_effect=forbid_legacy):
            self.assertEqual(hooks.project(self.root, "codex", apply=True, enabled=True)["status"], "unchanged")

    def test_copied_company_proof_regenerates_absolute_paths_without_trust(self):
        hooks.project(self.root, "codex", apply=True, enabled=True)
        relocated = Path(self.tmp.name).resolve() / "relocated company"
        shutil.copytree(self.root, relocated, symlinks=True)
        from checks.fixture_helpers import directory_projections
        directory_projections(relocated)
        result = hooks.project(relocated, "codex", apply=True, enabled=True)
        self.assertEqual(result["status"], "projected")
        self.assertIn("unverified", result["native"])
        import shlex
        for item in result["projection"]:
            args = shlex.split(item["group"]["hooks"][0]["command"])
            self.assertEqual(args[args.index("--root") + 1], str(relocated))
        self.assertFalse(config(relocated)["hooks"]["enabled"])
        self.assertTrue((relocated / ".system/hooks-project-codex.json").is_file())

    def test_legacy_proof_mismatch_and_malformed_preserve_all_bytes(self):
        hooks.project(self.root, "claude", apply=True, enabled=True)
        proof = self.root / ".system/hooks-project-claude.json"
        legacy = self.root / ".system/cache/hooks-project-claude.json"
        legacy.parent.mkdir(parents=True, exist_ok=True); shutil.move(proof, legacy)
        target = self.root / ".claude/settings.json"; value = load(target)
        value["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 2; write(target, value)
        before_target = target.read_bytes(); before_legacy = legacy.read_bytes()
        self.assertEqual(hooks.project(self.root, "claude", apply=True, enabled=True)["status"], "conflict")
        self.assertEqual(target.read_bytes(), before_target); self.assertEqual(legacy.read_bytes(), before_legacy)
        self.assertFalse(proof.exists())
        legacy.write_text("{malformed", encoding="utf-8", newline="\n")
        self.assertEqual(hooks.project(self.root, "claude", apply=True, enabled=True)["status"], "conflict")
        self.assertEqual(legacy.read_text(encoding="utf-8"), "{malformed"); self.assertEqual(target.read_bytes(), before_target)

    def test_interruption_between_settings_and_proof_detectable_on_retry(self):
        proof = self.root / ".system/hooks-project-codex.json"
        actual_write = hooks.write
        def interrupted(file, value):
            if file == proof:
                raise OSError("synthetic interruption before ownership proof")
            return actual_write(file, value)
        with patch.object(hooks, "write", side_effect=interrupted), self.assertRaises(OSError):
            hooks.project(self.root, "codex", apply=True, enabled=True)
        target = self.root / ".codex/hooks.json"; before = target.read_bytes()
        self.assertFalse(proof.exists())
        self.assertEqual(hooks.project(self.root, "codex", apply=True, enabled=True)["status"], "conflict")
        self.assertEqual(target.read_bytes(), before)

    def test_disabling_removes_exact_owned_groups_only(self):
        file = self.root / ".claude/settings.json"
        original = {"other": True, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo preserved"}]}]}}
        write(file, original)
        hooks.project(self.root, "claude", apply=True, enabled=True)
        self.assertEqual(hooks.project(self.root, "claude", apply=True, enabled=False)["status"], "projected")
        self.assertEqual(load(file), original)
        self.assertFalse(config(self.root)["hooks"]["enabled"])

    def test_malformed_native_settings_preserved_as_conflict(self):
        file = self.root / ".claude/settings.json"; file.parent.mkdir(exist_ok=True)
        for raw in ["{bad", "[]", '{"hooks":{"Stop":"wrong"}}', '{"hooks":{"Stop":[{"matcher":"x"}]}}']:
            file.write_text(raw, encoding="utf-8", newline="\n")
            self.assertEqual(hooks.project(self.root, "claude", apply=True, enabled=True)["status"], "conflict")
            self.assertEqual(file.read_text(encoding="utf-8"), raw)

    def test_concurrent_foreign_settings_edit_not_overwritten(self):
        file = self.root / ".claude/settings.json"; write(file, {"other": "initial"})
        original = copy.deepcopy
        def concurrent(value):
            write(file, {"other": "concurrent owner edit"})
            return original(value)
        with patch.object(hooks.copy, "deepcopy", side_effect=concurrent):
            result = hooks.project(self.root, "claude", apply=True, enabled=True)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(load(file), {"other": "concurrent owner edit"})
        self.assertFalse((self.root / ".system/hooks-project-claude.json").exists())

    def test_code_dependency_change_invalidates_command_and_old_dispatch(self):
        old = hooks.definition_hash(self.root, "codex")
        hooks.project(self.root, "codex", apply=True, enabled=True)
        file = self.root / "scripts/core.py"; file.write_text(file.read_text(encoding="utf-8") + "\n# changed helper\n", encoding="utf-8", newline="\n")
        self.assertNotEqual(hooks.definition_hash(self.root, "codex"), old)
        with self.assertRaisesRegex(Rejected, "fingerprint changed"):
            hooks.dispatch(self.root, "codex", "SessionStart", {}, definition_hash_expected=old)
        result = hooks.project(self.root, "codex", apply=True, enabled=True)
        self.assertEqual(result["status"], "projected")
        self.assertIn(result["definition_hash"], result["projection"][0]["group"]["hooks"][0]["command"])

    def test_native_protocol_versions_json_denial_and_advisory_both(self):
        self.enable()
        for harness, version in [("codex", "0.160.0"), ("claude", "2.1.289")]:
            with self.subTest(harness=harness):
                self.assertEqual(load(self.root / f"adapters/{harness}/profile.yaml")["hook_protocol"]["observed_version"], version)
                value = self.native(harness, "PreToolUse", self.write_payload("standards/wrong-name.md", self.standard()))
                self.assertEqual(value.returncode, 0, value.stdout + value.stderr)
                output = json.loads(value.stdout)["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], "PreToolUse")
                self.assertEqual(output["permissionDecision"], "deny")
                value = self.native(harness, "SessionStart", {"hook_event_name": "SessionStart"})
                self.assertEqual(value.returncode, 0)
                self.assertIn("additionalContext", json.loads(value.stdout)["hookSpecificOutput"])

    def test_native_invalid_json_oversize_missing_dependency_failure_not_pass(self):
        self.enable()
        expected = hooks.definition_hash(self.root, "codex")
        args = [sys.executable, str(self.root / "scripts/hooks.py"), "--root", str(self.root), "--namespace", hooks.NAMESPACE, "--harness", "codex", "--event", "SessionStart", "--definition-hash", expected]
        for raw in ["{bad", "x" * (hooks.MAX_INPUT + 1)]:
            value = subprocess.run(args, input=raw, text=True, capture_output=True, timeout=8)
            self.assertEqual(value.returncode, 1)
            self.assertIn("handler failure", json.loads(value.stdout)["systemMessage"])
        (self.root / "scripts/dependencies.py").unlink()
        value = subprocess.run(args, input="{}", text=True, capture_output=True, timeout=8)
        self.assertEqual(value.returncode, 1)
        self.assertIn("handler failure", json.loads(value.stdout)["systemMessage"])

    def test_native_timeout_explicit_failure_without_stop_block(self):
        self.enable()
        file = self.root / "scripts/hooks.py"
        file.write_text(file.read_text(encoding="utf-8").replace("    start = time.monotonic()", "    time.sleep(4)\n    start = time.monotonic()", 1), encoding="utf-8", newline="\n")
        value = self.native("claude", "Stop", {})
        self.assertEqual(value.returncode, 1)
        self.assertIn("TimeoutError", json.loads(value.stdout)["systemMessage"])
        self.assertNotIn('"decision"', value.stdout)

    def test_stop_compact_dedup_revision_no_business_writes(self):
        self.task(); file = self.root / "work/test-task/task.json"; before = file.read_bytes()
        first = hooks.dispatch(self.root, "common", "Stop", {}, "test-task")
        second = hooks.dispatch(self.root, "common", "PreCompact", {}, "test-task")
        self.assertEqual(first["status"], "advisory")
        self.assertEqual(second["messages"], [])
        self.assertEqual(file.read_bytes(), before)
        self.task(revision=1)
        self.assertEqual(hooks.dispatch(self.root, "common", "Stop", {}, "test-task")["status"], "advisory")
        with self.assertRaisesRegex(Rejected, "stale"): hooks.dispatch(self.root, "common", "Stop", {"task_revision": 0}, "test-task")
        output = hooks.native_output("Stop", first)
        self.assertEqual(set(output), {"systemMessage"})
        cache = (self.root / ".system/cache/hooks-continuation.json").read_text(encoding="utf-8")
        self.assertNotIn("Read current input", cache)

    def test_interrupt_subagent_recursive_stop_never_continues(self):
        self.task()
        for event, payload in [("Interrupt", {}), ("Stop", {"stop_hook_active": True}), ("Stop", {"subagent_id": "child"}), ("PreCompact", {"agent_type": "worker"}), ("Stop", {"interrupted": True})]:
            with self.subTest(event=event, payload=payload):
                result = hooks.dispatch(self.root, "common", event, payload, "test-task")
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(hooks.native_output(event, result), {})
        self.assertFalse((self.root / ".system/cache/hooks-continuation.json").exists())

    def test_unknown_task_navigation_only_disabled_scenario(self):
        result = hooks.dispatch(self.root, "common", "Stop", {}, "unknown-task")
        self.assertEqual(result["status"], "advisory")
        self.assertFalse((self.root / "work/unknown-task").exists())
        cfg = config(self.root); cfg["hooks"]["disabled"] = ["context-entry"]; write(self.root / "company/config.yaml", cfg)
        self.assertEqual(hooks.dispatch(self.root, "common", "SessionStart", {})["status"], "disabled")

    def test_method_impact_only_explicit_dependencies_and_publication_hint(self):
        self.task()
        payload = self.write_payload("standards/task-validation.md", (self.root / "standards/task-validation.md").read_text(encoding="utf-8"))
        result = hooks.dispatch(self.root, "common", "method-impact", payload)
        self.assertEqual(result["impact"][0]["task"], "test-task")
        result = hooks.dispatch(self.root, "common", "method-impact", self.write_payload("standards/unrelated.md", self.standard("unrelated")))
        self.assertEqual(result["impact"], [])
        result = hooks.dispatch(self.root, "common", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/system.py deliver remote"}})
        self.assertIn("history/path/policy", " ".join(result["messages"]))
        result = hooks.dispatch(self.root, "common", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "git push"}})
        self.assertEqual(result["messages"], [])

    def test_method_impact_explicit_batch_uses_declared_closure(self):
        self.task()
        result = hooks.dispatch(self.root, "common", "method-impact", {"paths": ["standards/task-validation.md"]})
        self.assertEqual(result["impact"][0]["task"], "test-task")
        self.assertTrue(result["method_consumers"])
        self.assertTrue(all(item["status"] == "explicit" for item in result["method_consumers"]))
        for paths in [[], "standards/task-validation.md", ["../outside.md"]]:
            with self.subTest(paths=paths), self.assertRaises(Rejected): hooks.dispatch(self.root, "common", "method-impact", {"paths": paths})

    def test_native_projection_apply_requires_company_permissions(self):
        cfg = config(self.root); cfg["permissions"]["local_work"] = False; write(self.root / "company/config.yaml", cfg)
        with self.assertRaisesRegex(Rejected, "authorized"): hooks.project(self.root, "codex", apply=True, enabled=True)
        self.assertFalse((self.root / ".codex/hooks.json").exists())
        self.assertEqual(hooks.project(self.root, "codex", enabled=True)["status"], "preview")

    def test_permission_revoked_by_final_fingerprint_preserves_settings_and_proof(self):
        hooks.project(self.root, "codex", apply=True, enabled=True)
        target = self.root / ".codex/hooks.json"; proof = self.root / ".system/hooks-project-codex.json"
        before_target = target.read_bytes(); before_proof = proof.read_bytes()
        method = self.root / "scripts/core.py"; method.write_text(method.read_text(encoding="utf-8") + "\n# method update awaiting projection\n", encoding="utf-8", newline="\n")
        original_hash = hooks.definition_hash
        calls = 0
        def revoke(root, harness):
            nonlocal calls
            value = original_hash(root, harness)
            calls += 1
            if calls == 2:
                cfg = config(root); cfg["permissions"]["local_work"] = False
                write(root / "company/config.yaml", cfg)
            return value
        with patch.object(hooks, "definition_hash", side_effect=revoke):
            result = hooks.project(self.root, "codex", apply=True, enabled=True)
        self.assertEqual(calls, 2)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(target.read_bytes(), before_target); self.assertEqual(proof.read_bytes(), before_proof)
        self.assertFalse(config(self.root)["permissions"]["local_work"])
        self.assertFalse(config(self.root)["hooks"]["enabled"])

    def test_config_changed_after_settings_write_never_forges_proof(self):
        target = self.root / ".claude/settings.json"; proof = self.root / ".system/hooks-project-claude.json"
        actual_write = hooks.write
        def revoke_after_settings(file, value):
            actual_write(file, value)
            if file == target:
                cfg = config(self.root); cfg["permissions"]["local_work"] = False
                actual_write(self.root / "company/config.yaml", cfg)
        with patch.object(hooks, "write", side_effect=revoke_after_settings):
            result = hooks.project(self.root, "claude", apply=True, enabled=True)
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(target.is_file()); self.assertFalse(proof.exists())
        before = target.read_bytes()
        self.assertFalse(config(self.root)["permissions"]["local_work"])
        self.assertFalse(config(self.root)["hooks"]["enabled"])
        cfg = config(self.root); cfg["permissions"]["local_work"] = True; write(self.root / "company/config.yaml", cfg)
        self.assertEqual(hooks.project(self.root, "claude", apply=True, enabled=True)["status"], "conflict")
        self.assertEqual(target.read_bytes(), before); self.assertFalse(proof.exists())

    def test_concurrent_cache_lock_failure_and_handler_crash_not_pass(self):
        self.task()
        from core import lock
        with lock(self.root), self.assertRaisesRegex(Rejected, "already running"):
            hooks.dispatch(self.root, "common", "Stop", {}, "test-task")
        with patch.object(hooks.validation, "entity_changes", side_effect=RuntimeError("fixture crash")), self.assertRaises(RuntimeError):
            hooks.dispatch(self.root, "common", "PreToolUse", self.write_payload("standards/new-standard.md", self.standard()))

    def test_product_root_routes_development_and_template_paths(self):
        product = Path(self.tmp.name).resolve() / "main product"
        product.mkdir(); shutil.move(str(self.root), product / "template")
        (product / "AGENTS.md").write_text("# Product\n", encoding="utf-8", newline="\n")
        (product / ".git").mkdir()
        (product / "scripts").mkdir(); (product / "scripts/product.py").write_text("# Product route marker\n", encoding="utf-8", newline="\n")
        (product / "docs/development/work/2026-10-06-hooks").mkdir(parents=True)
        (product / "docs/development/PLAN.md").write_text("# Plan\n", encoding="utf-8", newline="\n")
        (product / "docs/development/work/2026-10-06-hooks/task.md").write_text("# Existing owner\n", encoding="utf-8", newline="\n")
        result = hooks.dispatch(product, "common", "SessionStart", {}, "2026-10-06-hooks")
        self.assertIn("docs/development/work/2026-10-06-hooks/task.md", result["messages"][0])
        self.assertFalse((product / "work").exists())
        with self.assertRaisesRegex(Rejected, "preview-only"): hooks.project(product, "codex", apply=True, enabled=True)
        payload = {"cwd": str(product / "template"), "tool_name": "Write", "tool_input": {"file_path": "standards/wrong.md", "content": self.standard()}}
        self.assertEqual(hooks.dispatch(product, "common", "PreToolUse", payload)["status"], "proved-violation")


if __name__ == "__main__":
    unittest.main()
