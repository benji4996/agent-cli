from __future__ import annotations

from typer.testing import CliRunner

from cli.main import app


class DummyStrategy:
    def __init__(self, strategy_id: str, **kwargs):
        self.strategy_id = strategy_id


class DummyRiskManager:
    def __init__(self):
        self.configured = None

    def configure_gate(self, *, cooldown_duration_ms: int, cooldown_trigger_losses: int, cooldown_drawdown_pct: float, cooldown_close_losses: int):
        self.configured = {
            "cooldown_duration_ms": cooldown_duration_ms,
            "cooldown_trigger_losses": cooldown_trigger_losses,
            "cooldown_drawdown_pct": cooldown_drawdown_pct,
            "cooldown_close_losses": cooldown_close_losses,
        }


class DummyEngine:
    last = None

    def __init__(self, hl, strategy, instrument, tick_interval, dry_run, data_dir, risk_limits, builder, maker_refresh_interval_s=0.0, close_positions_on_shutdown=True, inventory_alert_qty=0.0):
        self.markout_tracker = None
        self.guard_config = None
        self.risk_manager = DummyRiskManager()
        self.maker_refresh_interval_s = maker_refresh_interval_s
        self.close_positions_on_shutdown = close_positions_on_shutdown
        self.inventory_alert_qty = inventory_alert_qty
        DummyEngine.last = self

    def run(self, max_ticks: int, resume: bool) -> None:
        return None


def test_run_applies_risk_gate_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "strategy: avellaneda_mm",
                "venue: paradex",
                "mainnet: true",
                "risk_gate:",
                "  cooldown_trigger_losses: 5",
                "  cooldown_duration_ms: 60000",
                "  cooldown_drawdown_pct: 80.0",
                "  cooldown_close_losses: 3",
            ]
        )
    )

    monkeypatch.setattr("cli.commands.run.build_venue_adapter", lambda venue, mainnet, mock, cfg=None: (object(), "LIVE"))
    monkeypatch.setattr("cli.strategy_registry.resolve_strategy_path", lambda strategy: "dummy:Strategy")
    monkeypatch.setattr("sdk.strategy_sdk.loader.load_strategy", lambda path: DummyStrategy)
    monkeypatch.setattr("cli.engine.TradingEngine", DummyEngine)

    result = CliRunner().invoke(app, ["run", "avellaneda_mm", "--config", str(cfg_path), "--fresh"])

    assert result.exit_code == 0, result.output
    assert DummyEngine.last is not None
    assert DummyEngine.last.risk_manager.configured == {
        "cooldown_duration_ms": 60000,
        "cooldown_trigger_losses": 5,
        "cooldown_drawdown_pct": 80.0,
        "cooldown_close_losses": 3,
    }
    assert "Risk gate: losses=5, cooldown_ms=60000, drawdown_pct=80.0, close_losses=3" in result.output
