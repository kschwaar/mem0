"""Execute startup config function without importing the database/model runtime."""

import ast
import json
import math
import os
from pathlib import Path

import pytest


def config_function():
    path = Path(__file__).parents[1] / "main.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_openai_provider_config")
    namespace = {"os": os, "json": json, "math": math, "Any": object,
                 "OPENAI_API_KEY": "fixture-key", "OPENAI_BASE_URL": "http://host.docker.internal:8080/v1",
                 "DEFAULT_LLM_MODEL": "fixture-model"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_openai_provider_config"]


@pytest.fixture(autouse=True)
def clear_settings(monkeypatch):
    for name in ("MAX_TOKENS", "MAX_RETRIES", "TEMPERATURE", "TOP_P", "REQUEST_TIMEOUT", "EXTRA_BODY_JSON"):
        monkeypatch.delenv("MEM0_LLM_" + name, raising=False)


def test_default_settings_unchanged():
    config = config_function()()
    assert config["temperature"] == 0.2
    assert "extra_body" not in config and "max_retries" not in config


def test_explicit_local_baseline_settings(monkeypatch):
    values = {"MAX_TOKENS": "256", "MAX_RETRIES": "0", "TEMPERATURE": "0", "TOP_P": "1",
              "REQUEST_TIMEOUT": "30", "EXTRA_BODY_JSON": '{"chat_template_kwargs":{"enable_thinking":false}}'}
    for name, value in values.items():
        monkeypatch.setenv("MEM0_LLM_" + name, value)
    config = config_function()()
    assert config["max_tokens"] == 256 and config["max_retries"] == 0
    assert config["temperature"] == 0 and config["top_p"] == 1 and config["request_timeout"] == 30
    assert config["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("name,value", [("MAX_TOKENS", "0"), ("MAX_RETRIES", "-1"),
    ("TEMPERATURE", "nan"), ("TOP_P", "2"), ("REQUEST_TIMEOUT", "0"), ("EXTRA_BODY_JSON", "[]")])
def test_invalid_settings_rejected(monkeypatch, name, value):
    monkeypatch.setenv("MEM0_LLM_" + name, value)
    with pytest.raises(ValueError):
        config_function()()
