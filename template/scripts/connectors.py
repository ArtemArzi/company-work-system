"""Read-only export, HTTP and MCP adapters sharing an evidence envelope."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from core import config, digest, load, path, require
from validation import source_envelope


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect requires a separately authorized endpoint")


class MCP:
    """Small stdio client. No shell, inferred credentials, installation or writes."""
    def __init__(self, specification):
        require(specification.get("approved_by") and isinstance(specification.get("command"), list) and specification["command"], "MCP transport not approved")
        self.timeout = specification["timeout_seconds"]
        self.process = subprocess.Popen(specification["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.messages = queue.Queue()
        self.sequence = 0
        def reader():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"error": "invalid MCP frame"})
            self.messages.put({"error": "MCP closed"})
        threading.Thread(target=reader, daemon=True).start()

    def call(self, method, params):
        self.sequence += 1
        self.send({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        for _ in range(100):
            try:
                response = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": self.sequence, "reason": "timeout"}})
                raise ValueError("MCP timeout; no write was issued") from exc
            require(not response.get("error"), "MCP error/closed")
            if response.get("id") == self.sequence:
                return response["result"]
        raise ValueError("MCP notification limit")

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()


def read(root, source_id):
    cfg = config(root)
    specification = cfg.get("sources", {}).get(source_id)
    require(specification and specification.get("approved_by"), "source/account/scope not configured")
    for field in ["account", "scope", "period", "unit", "max_age_seconds", "max_pages", "timeout_seconds"]:
        require(specification.get(field) is not None, f"source limit/definition missing: {field}")
    require(type(specification["max_pages"]) is int and specification["max_pages"] > 0 and specification["timeout_seconds"] > 0 and specification["max_age_seconds"] > 0, "invalid source limits")
    pages, cursor, cursors, client = [], None, set(), None
    try:
        if specification["type"] == "mcp":
            client = MCP(specification)
            initialized = client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "company-work-system", "version": "1.0.0"}})
            require(initialized.get("protocolVersion") in {"2024-11-05", "2025-03-26", "2025-06-18"}, "unsupported negotiated MCP protocol")
            require("tools" in initialized.get("capabilities", {}), "MCP tools capability not negotiated")
            client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = client.call("tools/list", {})
            tool = specification["read_tool"]
            require(tool in specification.get("allowed_read_tools", []) and any(t["name"] == tool for t in tools["tools"]), "MCP read tool missing/not allowed")
        for _ in range(specification["max_pages"]):
            if specification["type"] == "export":
                require(cursor is None, "export cannot claim unsupported pagination")
                value = load(path(root, specification["path"]))
            elif specification["type"] == "http":
                endpoint = specification["endpoint"]
                parsed = urllib.parse.urlparse(endpoint)
                require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password, "invalid/credential-bearing endpoint")
                suffix = urllib.parse.urlencode({"cursor": cursor}) if cursor is not None else ""
                url = endpoint + (("&" if "?" in endpoint else "?") + suffix if suffix else "")
                opener = urllib.request.build_opener(NoRedirect())
                try:
                    with opener.open(urllib.request.Request(url, method="GET"), timeout=specification["timeout_seconds"]) as response:
                        value = json.loads(response.read(8 * 1024 * 1024 + 1))
                except urllib.error.HTTPError as exc:
                    raise ValueError(f"HTTP {exc.code}: no blind retry") from exc
            elif specification["type"] == "mcp":
                response = client.call("tools/call", {"name": specification["read_tool"], "arguments": {"scope": specification["scope"], "period": specification["period"], "cursor": cursor}})
                require(not response.get("isError"), "MCP read failed")
                value = response.get("structuredContent")
                if value is None:
                    texts = [c["text"] for c in response.get("content", []) if c.get("type") == "text"]
                    require(len(texts) == 1, "MCP result without one structured envelope")
                    value = json.loads(texts[0])
            else:
                raise ValueError("unsupported source adapter")
            source_envelope(value, cfg["id"], specification)
            pages.append(value)
            cursor = value.get("next_cursor")
            if cursor is None:
                break
            require(isinstance(cursor, str) and cursor not in cursors, "repeated/invalid cursor")
            cursors.add(cursor)
        else:
            raise ValueError("pagination limit; source incomplete")
        result = {**pages[0], "records": [r for page in pages for r in page["records"]], "next_cursor": None}
        source_envelope(result, cfg["id"], specification)
        result["evidence_level"] = "file-export" if specification["type"] == "export" else "transport-read; provider/account trust depends on accepted configuration"
        result["pages"] = len(pages)
        return result
    finally:
        if client:
            client.close()
