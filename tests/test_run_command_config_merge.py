from __future__ import annotations

from typer.testing import CliRunner

from cli.main import app


class DummyStrategy:
    def __init__(self, strategy_id: str, **kwargs):
        self.strategy_id = strategy_id


class DummyEngine:
    captured = {}

    def __init__(self, hl, strategy, instrument, tick_interval, dry_run, data_dir, risk_limits, builder):
        self.markout_tracker = None
        DummyEngine.captured = {
            "instrument": instrument,
            "tick_interval": tick_interval,
            "dry_run": dry_run,
            "data_dir": data_dir,
            "strategy_id": strategy.strategy_id,
        }

    def run(self, max_ticks: int, resume: bool) -> None:
        DummyEngine.captured["max_ticks"] = max_ticks
        DummyEngine.captured["resume"] = resume


def test_run_uses_yaml_values_when_cli_flags_not_explicit(monkeypatch, tmp_path):
    cfg_path = tmp_path / "paradex.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "strategy: avellaneda_mm",
                "instrument: SOL-USD-PERP",
                "venue: paradex",
                "mainnet: true",
                "tick_interval: 2.0",
                f"data_dir: {tmp_path / 'custom-data'}",
                "max_ticks: 3",
                "dry_run: false",
            ]
        )
    )

    monkeypatch.setattr("cli.commands.run.build_venue_adapter", lambda venue, mainnet, mock, cfg=None: (object(), "LIVE"))
    monkeypatch.setattr("cli.strategy_registry.resolve_strategy_path", lambda strategy: "dummy:Strategy")
    monkeypatch.setattr("sdk.strategy_sdk.loader.load_strategy", lambda path: DummyStrategy)
    monkeypatch.setattr("cli.engine.TradingEngine", DummyEngine)

    result = CliRunner().invoke(app, ["run", "avellaneda_mm", "--config", str(cfg_path), "--fresh"])

    assert result.exit_code == 0, result.output
    assert DummyEngine.captured["instrument"] == "SOL-USD-PERP"
    assert DummyEngine.captured["tick_interval"] == 2.0
    assert DummyEngine.captured["data_dir"] == str(tmp_path / "custom-data")
    assert DummyEngine.captured["max_ticks"] == 3
    assert DummyEngine.captured["resume"] is False


def test_run_cli_flags_override_yaml_when_explicit(monkeypatch, tmp_path):
    cfg_path = tmp_path / "paradex.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "strategy: avellaneda_mm",
                "instrument: SOL-USD-PERP",
                "venue: paradex",
                "mainnet: false",
                "tick_interval: 2.0",
                f"data_dir: {tmp_path / 'custom-data'}",
                "max_ticks: 3",
            ]
        )
    )

    monkeypatch.setattr("cli.commands.run.build_venue_adapter", lambda venue, mainnet, mock, cfg=None: (object(), "LIVE"))
    monkeypatch.setattr("cli.strategy_registry.resolve_strategy_path", lambda strategy: "dummy:Strategy")
    monkeypatch.setattr("sdk.strategy_sdk.loader.load_strategy", lambda path: DummyStrategy)
    monkeypatch.setattr("cli.engine.TradingEngine", DummyEngine)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "avellaneda_mm",
            "--config",
            str(cfg_path),
            "--tick",
            "5",
            "--data-dir",
            str(tmp_path / "override-data"),
            "--mainnet",
            "--max-ticks",
            "7",
            "--fresh",
        ],
    )

    assert result.exit_code == 0, result.output
    assert DummyEngine.captured["tick_interval"] == 5.0
    assert DummyEngine.captured["data_dir"] == str(tmp_path / "override-data")
    assert DummyEngine.captured["max_ticks"] == 7
