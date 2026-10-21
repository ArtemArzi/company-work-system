"""Opt-in evidence, retained review and bounded search on synthetic research."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from checks.fixture_helpers import execute_with_current_tokens_for_fixture
from checks.test_system import Fixture
from core import Rejected, config, digest, load, object_hash, task_file, write
import knowledge
import operations
import research_evidence
import validation


class ResearchEvidence(Fixture):
    def setUp(self):
        super().setUp()
        self.source_path = "company/sources/research-input.txt"
        file = self.root / self.source_path
        file.write_bytes("First observation\r\nВторая строка\r\nRAW-SENTINEL-6382\r\n".encode("utf-8"))
        self.source_hash = digest(file)

    def result(self, outcome="supported"):
        finding = {"id": "claim-one", "claim": "Unique-result-claim", "source": self.source_path,
                   "level": "observation", "outcome": outcome}
        if outcome == "unknown":
            finding.update(level="unknown", gap="A second observation has not been collected")
        else:
            finding["evidence"] = [{"path": self.source_path, "sha256": self.source_hash,
                                    "start_line": 1, "end_line": 2,
                                    "quote": "First observation\nВторая строка"}]
        return {"schema_version": 1, "company_id": "test-company", "kind": "research",
                "summary": "A bounded synthetic conclusion", "next_action": "Check the stated condition",
                "limitations": ["Only a retained local observation"],
                "sources": [{"path": self.source_path, "sha256": self.source_hash}],
                "question": "Can the selected conclusion be reused?", "coverage": "One synthetic source",
                "findings": [finding], "research_evidence": {"schema_version": 1, "outcome": "findings",
                    "decision": "Reuse if the condition still holds", "conditions": ["Condition-needle"],
                    "gaps": ["Gap-needle"], "recheck": ["Recheck-needle"]}}

    def acceptance(self, process=False):
        return {"kind": "research", "research_evidence_version": 1,
                **({"research_review_process_required": True} if process else {})}

    def critical(self):
        return {"request_alignment": "The research retains its question, evidence and decision",
                "counterexample": "A matching quotation may still fail to support the interpretation",
                "limitations": ["Synthetic retained observation only"], "references": [self.source_path]}

    def review(self, artifact):
        review = {"reviewer": "synthetic-reviewer", "verdict": "pass", "artifact_hash": object_hash(artifact)}
        report = {"schema_version": 1, "run_id": "synthetic-review-run", **review,
                  "reviewed_claim_ids": [f["id"] for f in artifact["findings"]],
                  "challenge": "The retained quote may fail to support the claimed interpretation"}
        relative = "work/research-review/process.json"
        write(self.root / relative, report)
        review["process_reference"] = {"path": relative, "sha256": digest(self.root / relative)}
        return review

    def select(self, artifact=None):
        relative = "work/research-memory/result-1.json"
        write(self.root / relative, artifact or self.result())
        meta = {"schema_version": 1, "id": "research-memory", "owner": "test-owner",
                "origin": "Synthetic retained result", "status": "observation", "aliases": [],
                "summary": "MANIFEST-SENTINEL-7091", "links": [],
                "selected_result": {"path": relative, "sha256": digest(self.root / relative)}}
        manifest = "company/sources/research-memory/source.yaml"
        write(self.root / manifest, meta)
        return manifest, meta

    def test_supported_contradicted_unknown_and_utf8_crlf(self):
        for outcome in ["supported", "contradicted", "unknown"]:
            with self.subTest(outcome=outcome):
                self.assertTrue(validation.result(self.root, self.result(outcome), self.acceptance()))

    def test_legacy_absence_remains_valid_but_opt_in_requires_block(self):
        artifact = self.result()
        artifact.pop("research_evidence")
        for finding in artifact["findings"]:
            for field in ["id", "outcome", "evidence"]:
                finding.pop(field, None)
        self.assertTrue(validation.result(self.root, artifact, {"kind": "research"}))
        with self.assertRaisesRegex(Rejected, "evidence missing"):
            validation.result(self.root, artifact, self.acceptance())

    def test_supplied_version_always_validates_and_booleans_are_not_versions(self):
        for version in [2, True, None, "1"]:
            artifact = self.result()
            artifact["research_evidence"]["schema_version"] = version
            with self.subTest(version=version), self.assertRaisesRegex(Rejected, "evidence format"):
                validation.result(self.root, artifact, {"kind": "research"})
        with self.assertRaisesRegex(Rejected, "accepted research evidence version"):
            validation.result(self.root, self.result(), {"kind": "research", "research_evidence_version": 2})

    def test_empty_no_findings_and_inconclusive_need_reason(self):
        for outcome in ["no-findings", "inconclusive"]:
            artifact = self.result()
            artifact["findings"] = []
            artifact["research_evidence"]["outcome"] = outcome
            with self.subTest(outcome=outcome), self.assertRaisesRegex(Rejected, "explicit reason"):
                validation.result(self.root, artifact, self.acceptance())
            artifact["research_evidence"]["reason"] = "No answer within the declared coverage"
            self.assertTrue(validation.result(self.root, artifact, self.acceptance()))

    def test_empty_findings_outcome_and_nonempty_no_findings_rejected(self):
        artifact = self.result()
        artifact["research_evidence"]["outcome"] = "no-findings"
        with self.assertRaisesRegex(Rejected, "contains findings"):
            validation.result(self.root, artifact, self.acceptance())
        artifact["research_evidence"]["outcome"] = "findings"
        artifact["findings"] = []
        with self.assertRaisesRegex(Rejected, "needs findings"):
            validation.result(self.root, artifact, self.acceptance())

    def test_decision_conditions_gaps_recheck_and_unique_ids(self):
        for field, bad in [("decision", " "), ("conditions", None), ("gaps", [""]), ("recheck", "later")]:
            artifact = self.result()
            artifact["research_evidence"][field] = bad
            with self.subTest(field=field), self.assertRaises(Rejected):
                validation.result(self.root, artifact, self.acceptance())
        artifact = self.result()
        artifact["findings"].append(copy.deepcopy(artifact["findings"][0]))
        with self.assertRaisesRegex(Rejected, "duplicate research claim"):
            validation.result(self.root, artifact, self.acceptance())

    def test_supported_and_contradicted_need_spans_unknown_needs_gap(self):
        for outcome in ["supported", "contradicted", "unknown"]:
            artifact = self.result(outcome)
            artifact["findings"][0].pop("gap" if outcome == "unknown" else "evidence")
            with self.subTest(outcome=outcome), self.assertRaisesRegex(Rejected, "needs"):
                validation.result(self.root, artifact, self.acceptance())

    def test_quote_bounds_and_source_declaration_must_match(self):
        mutations = [("quote", "Invented quote"), ("start_line", 0), ("start_line", True),
                     ("end_line", 100), ("end_line", 0), ("end_line", 1.5),
                     ("path", "company/sources/undeclared.txt"), ("sha256", "0" * 64)]
        for field, bad in mutations:
            artifact = self.result()
            artifact["findings"][0]["evidence"][0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(Rejected):
                validation.result(self.root, artifact, self.acceptance())

    def test_changed_raw_bytes_including_eol_are_rejected(self):
        artifact = self.result()
        file = self.root / self.source_path
        file.write_bytes(file.read_bytes().replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(Rejected, "source changed"):
            validation.result(self.root, artifact, self.acceptance())
        with self.assertRaisesRegex(Rejected, "source changed"):
            research_evidence.validate_research(self.root, artifact, self.acceptance())

    def test_review_process_exact_bytes_artifact_and_known_claims(self):
        artifact = self.result()
        review = self.review(artifact)
        self.assertTrue(research_evidence.validate_review(self.root, review, artifact))
        changed = {**artifact, "summary": "Changed result"}
        with self.assertRaisesRegex(Rejected, "artifact mismatch"):
            research_evidence.validate_review(self.root, review, changed)
        file = self.root / review["process_reference"]["path"]
        file.write_bytes(file.read_bytes() + b"\n")
        with self.assertRaisesRegex(Rejected, "report changed"):
            research_evidence.validate_review(self.root, review, artifact)

    def test_review_process_mismatches_and_missing_challenge_rejected(self):
        artifact = self.result()
        for field, bad in [("reviewer", "another-reviewer"), ("verdict", "fail"),
                           ("artifact_hash", "0" * 64), ("reviewed_claim_ids", ["alien-claim"]),
                           ("reviewed_claim_ids", ["claim-one", "claim-one"]), ("reviewed_claim_ids", []),
                           ("challenge", ""), ("run_id", ""), ("schema_version", 2)]:
            review = self.review(artifact)
            file = self.root / review["process_reference"]["path"]
            report = load(file)
            report[field] = bad
            write(file, report)
            review["process_reference"]["sha256"] = digest(file)
            with self.subTest(field=field), self.assertRaises(Rejected):
                research_evidence.validate_review(self.root, review, artifact)

    def test_legacy_independent_review_and_enhanced_process_execute(self):
        for task_id, process in [("legacy-research-task", False), ("enhanced-research-task", True)]:
            artifact = self.result()
            review = self.review(artifact)
            if not process:
                review.pop("process_reference")
            operations.intake(self.root, task_id, "Review synthetic research", "test-owner",
                              self.acceptance(process), True, inputs=[self.source_path], independent_required=True)
            task = execute_with_current_tokens_for_fixture(self.root, task_id, artifact, self.critical(), review)
            self.assertEqual(task["status"], "verified")
            self.assertTrue(validation.task(self.root, task))
            if process:
                file = self.root / review["process_reference"]["path"]
                file.write_bytes(file.read_bytes() + b"\n")
                with self.assertRaisesRegex(Rejected, "report changed"):
                    validation.task(self.root, task, freshness=False)

    def test_acceptance_requires_process_report_and_rejects_bad_optional_ref(self):
        for task_id, required in [("required-process", True), ("optional-process", False)]:
            artifact = self.result()
            review = self.review(artifact)
            if required:
                review.pop("process_reference")
            else:
                review["process_reference"]["sha256"] = "0" * 64
            operations.intake(self.root, task_id, "Review synthetic research", "test-owner",
                              self.acceptance(required), True, inputs=[self.source_path], independent_required=True)
            with self.subTest(required=required), self.assertRaisesRegex(Rejected, "process report"):
                execute_with_current_tokens_for_fixture(self.root, task_id, artifact, self.critical(), review)
            self.assertEqual(load(task_file(self.root, task_id))["status"], "blocked")

    def test_selected_result_new_process_canonical_pin_and_graph_off_parity(self):
        _, meta = self.select()
        first = self.cli("search", "Unique-result-claim")
        self.assertEqual(len(first["hits"]), 1)
        self.assertEqual(first["hits"][0]["selected_result"], meta["selected_result"])
        self.assertTrue(any("freshness" in limitation for limitation in first["hits"][0]["result_limitations"]))
        cfg = config(self.root)
        cfg["features"]["graph"] = False
        write(self.root / "company/config.yaml", cfg)
        second = self.cli("search", "Unique-result-claim")
        self.assertEqual(first["hits"], second["hits"])
        self.assertEqual(second["mode"], "direct-source")
        for query in ["Condition-needle", "Gap-needle", "Recheck-needle"]:
            self.assertEqual(len(knowledge.search(self.root, query)["hits"]), 1)

    def test_selected_result_excludes_raw_quotes_and_arbitrary_fields(self):
        artifact = self.result()
        artifact["extra"] = "EXTRA-SENTINEL-2712"
        artifact["coverage"] = "COVERAGE-SENTINEL-3092"
        self.select(artifact)
        raw_file = self.root / self.source_path
        original = Path.read_bytes
        def restricted(file):
            self.assertNotEqual(file, raw_file, "Search read a primary source")
            return original(file)
        with patch.object(Path, "read_bytes", restricted):
            for query in ["RAW-SENTINEL-6382", "First observation", "EXTRA-SENTINEL-2712",
                          "COVERAGE-SENTINEL-3092", "MANIFEST-SENTINEL-7091"]:
                self.assertFalse(knowledge.search(self.root, query)["hits"])
        raw_file.unlink()
        self.assertEqual(len(knowledge.search(self.root, "Unique-result-claim")["hits"]), 1)

    def test_selected_result_changed_pin_foreign_company_kind_and_ambiguous_material(self):
        manifest, meta = self.select()
        selected_file = self.root / meta["selected_result"]["path"]
        selected_file.write_bytes(selected_file.read_bytes() + b"\n")
        with self.assertRaisesRegex(Rejected, "Result changed"):
            knowledge.search(self.root, "claim")
        for field, bad in [("company_id", "foreign-company"), ("kind", "metrics")]:
            artifact = self.result()
            artifact[field] = bad
            manifest, meta = self.select(artifact)
            with self.subTest(field=field), self.assertRaisesRegex(Rejected, "company/kind"):
                knowledge.search(self.root, "claim")
        manifest, meta = self.select()
        meta["material"] = self.source_path
        write(self.root / manifest, meta)
        with self.assertRaisesRegex(Rejected, "combine material"):
            knowledge.search(self.root, "claim")
        with self.assertRaisesRegex(Rejected, "combine material"):
            validation.entity_contract(self.root, manifest, "source", meta)

    def test_selected_result_source_validation_and_legacy_result(self):
        artifact = self.result()
        artifact.pop("research_evidence")
        for finding in artifact["findings"]:
            for field in ["id", "outcome", "evidence"]:
                finding.pop(field)
        manifest, meta = self.select(artifact)
        self.assertEqual(validation.entity_contract(self.root, manifest, "source", meta), "research-memory")
        self.assertEqual(len(knowledge.search(self.root, "Unique-result-claim")["hits"]), 1)
        artifact["findings"][0]["source"] = "undeclared.txt"
        manifest, meta = self.select(artifact)
        with self.assertRaisesRegex(Rejected, "qualified source"):
            validation.entity_contract(self.root, manifest, "source", meta)

    def test_selected_result_is_pinned_in_knowledge_task_dependencies(self):
        from core import changed_bindings
        _, meta = self.select()
        task = operations.intake(self.root, "knowledge-dependency", "Reuse selected research", "test-owner",
                                 {"kind": "research"}, True, skill="company-knowledge")
        self.assertIn(meta["selected_result"]["path"], task["binding_scope"]["paths"])
        self.assertIn("scripts/research_evidence.py", task["binding_scope"]["paths"])
        selected = self.root / meta["selected_result"]["path"]
        selected.write_bytes(selected.read_bytes() + b"\n")
        self.assertTrue(changed_bindings(self.root, task["bindings"], task["binding_scope"]))

    def test_process_and_selected_result_references_use_company_path_safety(self):
        artifact = self.result()
        review = self.review(artifact)
        for bad in ["../escape.json", "/tmp/escape.json", "work//result.json"]:
            with self.subTest(path=bad):
                review["process_reference"]["path"] = bad
                with self.assertRaises(Rejected):
                    research_evidence.validate_review(self.root, review, artifact)
                _, meta = self.select()
                meta["selected_result"]["path"] = bad
                with self.assertRaises(Rejected):
                    research_evidence.selected_result(self.root, meta)
