from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from cli.engine import TradingEngine
from parent.position_tracker import PositionTracker


class _HLStub:
    def __init__(self, account_state):
        self._account_state = account_state
        self.placed = []

    def get_account_state(self):
        return self._account_state

    def get_snapshot(self, instrument):
        return SimpleNamespace(bid=100.0, ask=101.0)

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return None

    def collect_new_fills(self, instrument):
        return []


def _make_engine(hl):
    engine = TradingEngine.__new__(TradingEngine)
    engine.hl = hl
    engine.strategy = SimpleNamespace(strategy_id="avellaneda_mm")
    engine.instrument = "SOL-USD-PERP"
    engine.position_tracker = PositionTracker()
    engine.dry_run = False
    engine.builder = None
    engine._apply_fills = lambda fills, meta=None: None
    return engine


def test_sync_positions_maps_short_side_to_negative_qty():
    hl = _HLStub(
        {
            "positions": [
                {
                    "market": "SOL-USD-PERP",
                    "side": "SHORT",
                    "size": "0.26",
                    "average_entry_price": "86.01",
                    "realized_positional_pnl": "0",
                    "realized_positional_funding_pnl": "0",
                }
            ]
        }
    )
    engine = _make_engine(hl)

    engine._sync_positions_from_exchange()

    pos = engine.position_tracker.get_agent_position("avellaneda_mm", "SOL-USD-PERP")
    assert pos.net_qty == Decimal("-0.26")
    assert pos.avg_entry_price == Decimal("86.01")


def test_sync_positions_clears_local_position_when_exchange_is_flat():
    hl = _HLStub({"positions": []})
    engine = _make_engine(hl)
    engine.position_tracker.apply_fill("avellaneda_mm", "SOL-USD-PERP", "buy", Decimal("0.12"), Decimal("86.0"))

    engine._sync_positions_from_exchange()

    pos = engine.position_tracker.get_agent_position("avellaneda_mm", "SOL-USD-PERP")
    assert pos.net_qty == Decimal("0")


def test_close_all_positions_uses_synced_exchange_sign_before_shutdown_order():
    hl = _HLStub(
        {
            "positions": [
                {
                    "market": "SOL-USD-PERP",
                    "side": "LONG",
                    "size": "0.26",
                    "average_entry_price": "86.01",
                    "realized_positional_pnl": "0",
                    "realized_positional_funding_pnl": "0",
                }
            ]
        }
    )
    engine = _make_engine(hl)
    # stale opposite local state that used to cause the wrong shutdown side
    engine.position_tracker.apply_fill("avellaneda_mm", "SOL-USD-PERP", "sell", Decimal("0.26"), Decimal("86.01"))

    engine._close_all_positions()

    assert hl.placed, "expected shutdown order to be submitted"
    assert hl.placed[0]["side"] == "sell"
    assert hl.placed[0]["tif"] == "Ioc"
    assert hl.placed[0]["reduce_only"] is True
