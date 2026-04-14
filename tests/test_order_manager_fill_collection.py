from __future__ import annotations

from decimal import Decimal

from cli.order_manager import OrderManager
from common.models import MarketSnapshot


class FakeVenue:
    def __init__(self):
        self.collected = 0
        self.placed = []

    def get_open_orders(self, instrument=""):
        return []

    def cancel_order(self, instrument, oid):
        return True

    def place_order(self, instrument, side, size, price, tif="Ioc", builder=None):
        self.placed.append((instrument, side, size, price, tif))
        return None

    def collect_new_fills(self, instrument=""):
        if self.collected == 0:
            self.collected += 1
            return []
        self.collected += 1
        return [
            type("Fill", (), {
                "oid": "fill-1",
                "instrument": instrument,
                "side": "buy",
                "price": 83.8,
                "quantity": 0.15,
                "timestamp_ms": 1234,
                "fee": 0.0,
            })()
        ]


def test_order_manager_collects_exchange_fills_after_placement():
    venue = FakeVenue()
    manager = OrderManager(venue, instrument="SOL-USD-PERP", dry_run=False)
    decision = type("Decision", (), {
        "action": "place_order",
        "side": "buy",
        "size": 0.15,
        "limit_price": 83.8,
        "instrument": "SOL-USD-PERP",
        "order_type": "Gtc",
        "meta": {},
    })()
    snapshot = MarketSnapshot(
        instrument="SOL-USD-PERP",
        mid_price=83.85,
        bid=83.84,
        ask=83.86,
        spread_bps=2.0,
        timestamp_ms=1234,
    )
    fills = manager.update([decision], snapshot)
    assert len(venue.placed) == 1
    assert len(fills) == 1
    assert fills[0].oid == "fill-1"
    assert manager.stats["total_placed"] == 1
    assert manager.stats["total_filled"] == 1
