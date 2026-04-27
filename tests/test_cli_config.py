from __future__ import annotations

from cli.config import TradingConfig


def test_get_execution_value_uses_yaml_value_when_env_missing(monkeypatch):
    monkeypatch.delenv("PARADEX_HTTP_TIMEOUT_S", raising=False)
    cfg = TradingConfig(execution={"paradex_http_timeout_s": 12.5})
    assert cfg.get_execution_value("paradex_http_timeout_s", 15.0, float) == 12.5


def test_get_execution_value_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("PARADEX_HTTP_TIMEOUT_S", "19.75")
    cfg = TradingConfig(execution={"paradex_http_timeout_s": 12.5})
    assert cfg.get_execution_value("paradex_http_timeout_s", 15.0, float) == 19.75


def test_get_execution_value_returns_default_when_missing(monkeypatch):
    monkeypatch.delenv("PARADEX_HTTP_TIMEOUT_S", raising=False)
    cfg = TradingConfig()
    assert cfg.get_execution_value("paradex_http_timeout_s", 15.0, float) == 15.0
