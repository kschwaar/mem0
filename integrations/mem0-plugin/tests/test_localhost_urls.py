from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_api_url_defaults_to_hosted(monkeypatch):
    from _api import api_url

    monkeypatch.delenv("MEM0_BASE_URL", raising=False)

    assert api_url("/v3/memories/search/") == "https://api.mem0.ai/v3/memories/search/"


def test_api_url_uses_mem0_base_url(monkeypatch):
    from _api import api_url

    monkeypatch.setenv("MEM0_BASE_URL", "http://localhost:8888/")

    assert api_url("/v3/memories/search/") == "http://localhost:8888/v3/memories/search/"


def test_search_helper_uses_mem0_base_url(monkeypatch):
    from _search import search_memories

    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.read.return_value = json.dumps({"results": []}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setenv("MEM0_BASE_URL", "http://localhost:8888")
    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        search_memories("key", "user", "project", "query")

    assert captured["url"] == "http://localhost:8888/v3/memories/search/"


def test_mcp_templates_reference_mem0_mcp_url():
    root = Path(__file__).resolve().parents[1]
    for rel in [".codex-mcp.json", ".cursor-mcp.json", ".mcp.json", "mcp_config.json"]:
        assert "MEM0_MCP_URL" in (root / rel).read_text()


def test_setup_categories_skips_self_hosted_server(monkeypatch, capsys):
    from setup_coding_categories import main

    monkeypatch.setenv("MEM0_BASE_URL", "http://localhost:8888")
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["setup_coding_categories.py"])

    assert main() == 0
    assert "Platform-only feature" in capsys.readouterr().err


def test_auto_setup_categories_skips_self_hosted_server(monkeypatch):
    import auto_setup_categories

    monkeypatch.setenv("MEM0_BASE_URL", "http://localhost:8888")
    monkeypatch.setattr(
        auto_setup_categories,
        "resolve_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("resolve_api_key should not be called")),
    )

    auto_setup_categories.main()
