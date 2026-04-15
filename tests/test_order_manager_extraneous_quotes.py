from __future__ import annotations

from cli.order_manager import OrderManager
from common.models import MarketSnapshot, StrategyDecision


class FakeVenue:
    def __init__(self):
        self.open_orders = []
        self.cancelled = []
        self.placed = []

    def get_open_orders(self, instrument=""):
        return list(self.open_orders)

    def cancel_order(self, instrument, oid):
        self.cancelled.append((instrument, oid))
        return True

    def place_order(self, instrument, side, size, price, tif="Ioc", builder=None):
        self.placed.append((instrument, side, size, price, tif))
        return None

    def collect_new_fills(self, instrument=""):
        return []


def _snapshot(ts=1000):
    return MarketSnapshot(
        instrument="SOL-USD-PERP",
        mid_price=83.35,
        bid=83.34,
        ask=83.36,
        spread_bps=2.0,
        timestamp_ms=ts,
    )


def test_passive_refresh_cancels_extraneous_quote_sides_even_before_interval_expires():
    venue = FakeVenue()
    manager = OrderManager(venue, instrument="SOL-USD-PERP", maker_refresh_interval_s=12.0)
    decision = StrategyDecision(
        action="place_order",
        instrument="SOL-USD-PERP",
        side="sell",
        size=0.12,
        limit_price=83.35,
        order_type="Alo",
    )

    manager.update([decision], _snapshot(ts=1_000))

    venue.placed.clear()
    venue.cancelled.clear()
    venue.open_orders = [
        {"id": "buy-resting", "side": "BUY"},
        {"id": "sell-resting", "side": "SELL"},
    ]

    fills = manager.update([decision], _snapshot(ts=3_000))

    assert fills == []
    assert ("SOL-USD-PERP", "buy-resting") in venue.cancelled
    assert ("SOL-USD-PERP", "sell-resting") in venue.cancelled
    assert venue.placed == [("SOL-USD-PERP", "sell", 0.12, 83.35, "Alo")]
