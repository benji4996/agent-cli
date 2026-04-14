from __future__ import annotations

from cli.config import TradingConfig
from cli.venue_factory import build_venue_adapter


class DummyProxy:
    def connect(self):
        return None


def test_trading_config_loads_execution_options(tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "venue: paradex",
                "execution:",
                "  paradex_min_notional_mode: auto_bump",
                "  paradex_auto_bump_buffer_pct: 12.5",
            ]
        )
    )
    cfg = TradingConfig.from_yaml(str(cfg_path))
    assert cfg.execution["paradex_min_notional_mode"] == "auto_bump"
    assert cfg.execution["paradex_auto_bump_buffer_pct"] == 12.5


def test_build_venue_adapter_passes_paradex_execution_options(monkeypatch):
    created = {}

    class DummyAdapter:
        def __init__(self, proxy, *, min_notional_mode="strict", auto_bump_buffer_pct=0.0):
            created["proxy"] = proxy
            created["min_notional_mode"] = min_notional_mode
            created["auto_bump_buffer_pct"] = auto_bump_buffer_pct

    cfg = TradingConfig(
        venue="paradex",
        execution={
            "paradex_min_notional_mode": "auto_bump",
            "paradex_auto_bump_buffer_pct": 7.5,
        },
    )

    monkeypatch.setattr("adapters.paradex_adapter.ParadexVenueAdapter", DummyAdapter)
    monkeypatch.setattr("cli.config.TradingConfig.get_private_key", lambda self: "0x1234")
    monkeypatch.setattr("common.credentials.resolve_wallet_address", lambda venue: "0x" + "1" * 62)
    monkeypatch.setattr("parent.paradex_proxy.ParadexProxy", lambda **kwargs: DummyProxy())

    adapter, mode = build_venue_adapter(venue="paradex", mainnet=True, mock=False, cfg=cfg)
    assert mode == "LIVE (mainnet)"
    assert created["min_notional_mode"] == "auto_bump"
    assert created["auto_bump_buffer_pct"] == 7.5
