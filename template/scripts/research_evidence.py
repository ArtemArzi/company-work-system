"""Opt-in research evidence and bounded reuse of a pinned Result.

Exact quotes prove the retained text, not semantic entailment or reviewer identity.
Search reads the selected Result only; it does not recheck its primary sources.
"""
import hashlib
import json
import re

from core import config, object_hash, path, require

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _texts(value, label):
    require(isinstance(value, list) and all(_text(item) for item in value), label + " must be a list of nonempty strings")


def _reference(value, label):
    require(isinstance(value, dict) and _text(value.get("path")), label + " path missing")
    require(isinstance(value.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), label + " hash missing/invalid")
    return value


def _pinned(root, reference, label):
    reference = _reference(reference, label)
    file = path(root, reference["path"])
    require(file.is_file(), label + " missing")
    with file.open("rb") as stream:
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    require(len(raw) <= MAX_EVIDENCE_BYTES, label + " exceeds evidence read bound")
    require(hashlib.sha256(raw).hexdigest() == reference["sha256"], label + " changed")
    return raw


def _research_shape(value, acceptance):
    """Check declarations without reading primary files; return declared spans."""
    if "research_evidence_version" in acceptance:
        require(type(acceptance["research_evidence_version"]) is int and acceptance["research_evidence_version"] == 1, "unsupported accepted research evidence version")
    if "research_review_process_required" in acceptance:
        require(type(acceptance["research_review_process_required"]) is bool, "research review process requirement must be boolean")
    if "research_evidence" not in value:
        require("research_evidence_version" not in acceptance, "accepted research evidence missing")
        return []
    block = value["research_evidence"]
    require(isinstance(block, dict) and type(block.get("schema_version")) is int and block["schema_version"] == 1, "unsupported research evidence format")
    findings = value.get("findings")
    require(isinstance(findings, list), "research findings must be a list")
    require(block.get("outcome") in {"findings", "no-findings", "inconclusive"}, "research outcome missing")
    require(block["outcome"] != "findings" or bool(findings), "findings outcome needs findings")
    require(block["outcome"] != "no-findings" or not findings, "no-findings outcome contains findings")
    require(findings or _text(block.get("reason")), "empty research needs an explicit reason")
    require(_text(block.get("decision")), "research decision missing")
    for field in ["conditions", "gaps", "recheck"]:
        _texts(block.get(field), "research " + field)
    sources = {}
    for ref in value.get("sources", []):
        _reference(ref, "research source")
        require(ref["path"] not in sources or sources[ref["path"]] == ref["sha256"], "conflicting research source hashes")
        sources[ref["path"]] = ref["sha256"]
    seen, spans = set(), []
    for finding in findings:
        require(isinstance(finding, dict) and _text(finding.get("id")), "research claim ID missing")
        require(finding["id"] not in seen, "duplicate research claim ID")
        seen.add(finding["id"])
        require(_text(finding.get("claim")) and finding.get("outcome") in {"supported", "contradicted", "unknown"}, "research claim/outcome missing")
        evidence = finding.get("evidence", [])
        require(isinstance(evidence, list), "research claim evidence must be a list")
        if finding["outcome"] == "unknown":
            require(_text(finding.get("gap")), "unknown research claim needs a gap")
        else:
            require(evidence, "supported/contradicted research claim needs evidence spans")
        for span in evidence:
            _reference(span, "research span")
            require(sources.get(span["path"]) == span["sha256"], "research span differs from declared source")
            start, end = span.get("start_line"), span.get("end_line")
            require(type(start) is int and type(end) is int and 1 <= start <= end, "research span line bounds invalid")
            require(_text(span.get("quote")), "research span quote missing")
            spans.append(span)
    return spans


def validate_research(root, value, acceptance):
    """Validate new contracts when supplied; legacy Results need no migration."""
    spans = _research_shape(value, acceptance)
    sources = {}
    for span in spans:
        relative = span["path"]
        if relative not in sources:
            sources[relative] = _pinned(root, span, "research span source").decode("utf-8").splitlines()
        lines = sources[relative]
        require(span["end_line"] <= len(lines), "research span line bounds outside source")
        require("\n".join(lines[span["start_line"] - 1:span["end_line"]]) == span["quote"], "research span quote differs from source")
    return True


def validate_review(root, review, artifact):
    """Bind a retained process report to the exact reviewed artifact and claims."""
    if "process_reference" not in review:
        return True
    raw = _pinned(root, review["process_reference"], "research review process report")
    report = json.loads(raw.decode("utf-8"))
    require(isinstance(report, dict) and type(report.get("schema_version")) is int and report["schema_version"] == 1, "unsupported research review process format")
    require(_text(report.get("run_id")) and _text(report.get("reviewer")), "research review process identity missing")
    require(report["reviewer"] == review.get("reviewer") and report.get("verdict") == review.get("verdict") and report.get("verdict") in {"pass", "fail", "inconclusive"}, "research review process differs from review")
    require(report.get("artifact_hash") == review.get("artifact_hash") == object_hash(artifact), "research review process artifact mismatch")
    ids = report.get("reviewed_claim_ids")
    _texts(ids, "reviewed claim IDs")
    require(len(ids) == len(set(ids)), "duplicate reviewed claim ID")
    known = {finding["id"] for finding in artifact.get("findings", []) if isinstance(finding, dict) and isinstance(finding.get("id"), str)}
    require(set(ids) <= known, "research review process contains unknown claim ID")
    if artifact.get("research_evidence"):
        require(set(ids) == known, "research review process must cover the enhanced claim IDs")
    require(_text(report.get("challenge")), "research review process challenge missing")
    return True


def selected_result(root, meta):
    """Return a search projection; never open primary source paths from a Result."""
    require("selected_result" in meta, "selected research Result missing")
    require("material" not in meta, "source cannot combine material and selected_result")
    reference = _reference(meta["selected_result"], "selected research Result")
    value = json.loads(_pinned(root, reference, "selected research Result").decode("utf-8"))
    require(isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 1, "selected research Result format")
    require(value.get("company_id") == config(root)["id"] and value.get("kind") == "research", "selected Result company/kind mismatch")
    require(_text(value.get("question")) and _text(value.get("summary")) and _text(value.get("next_action")) and value.get("coverage"), "selected research Result incomplete")
    _texts(value.get("limitations"), "selected Result limitations")
    refs = value.get("sources")
    require(isinstance(refs, list) and refs, "selected research Result has no source declarations")
    for ref in refs:
        _reference(ref, "selected Result source")
        path(root, ref["path"])  # Validate containment, without reading raw sources.
    findings = value.get("findings")
    require(isinstance(findings, list), "selected research findings missing")
    allowed_refs = {ref["path"] for ref in refs}
    require(all(isinstance(finding, dict) and _text(finding.get("claim")) and finding.get("source") in allowed_refs and finding.get("level") in {"observation", "inference", "unknown"} for finding in findings), "selected research claim without qualified source")
    _research_shape(value, {})
    text = [value["question"], value["summary"]]
    for finding in findings:
        text.append(finding["claim"])
        if "outcome" in finding:
            text.append(finding["outcome"])
        if "gap" in finding:
            text.append(finding["gap"])
    block = value.get("research_evidence", {})
    if block:
        text.extend([block["outcome"], block["decision"]])
        for field in ["conditions", "gaps", "recheck"]:
            text.extend(block[field])
    return {"path": reference["path"], "sha256": reference["sha256"], "text": "\n".join(text),
            "limitations": [*value["limitations"], "Pinned Result only; primary source freshness and continued applicability are not rechecked by search", "Exact evidence spans do not establish semantic entailment or independent reviewer identity"]}
