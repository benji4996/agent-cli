from common.cross_venue_models import CrossVenueDecision
from cli.cross_venue_order_manager import CrossVenueOrderManager


class FakeAdapter:
    def __init__(self, fills=None, place_fill=None):
        self.orders = []
        self.cancelled = 0
        self._fills = fills or []
        self._place_fill = place_fill

    def get_open_orders(self, instrument=""):
        return []

    def cancel_order(self, instrument, oid):
        self.cancelled += 1
        return True

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return self._place_fill

    def collect_new_fills(self, instrument=""):
        return list(self._fills)


class Fill:
    def __init__(self, quantity):
        self.quantity = quantity


def _decision():
    return CrossVenueDecision(
        pair_id="p1",
        buy_venue="hl",
        sell_venue="paradex",
        buy_instrument="SOL-PERP",
        sell_instrument="SOL-USD-PERP",
        size=0.1,
        buy_price=100.0,
        sell_price=101.0,
        order_type="Ioc",
        expected_edge_bps=10.0,
    )


def test_cross_venue_order_manager_places_both_legs_and_reconciles_full_fills():
    hl = FakeAdapter(fills=[Fill(0.1)])
    paradex = FakeAdapter(fills=[Fill(0.1)])
    manager = CrossVenueOrderManager({"hl": hl, "paradex": paradex}, max_residual_qty=0.01)

    result = manager.execute(_decision())

    assert len(hl.orders) == 1
    assert hl.orders[0]["side"] == "buy"
    assert len(paradex.orders) == 1
    assert paradex.orders[0]["side"] == "sell"
    assert result.status == "filled"
    assert result.residual_qty == 0.0


def test_cross_venue_order_manager_flags_hedge_mismatch_when_one_leg_misses():
    hl = FakeAdapter(fills=[Fill(0.1)])
    paradex = FakeAdapter(fills=[])
    manager = CrossVenueOrderManager({"hl": hl, "paradex": paradex}, max_residual_qty=0.01, hedge_repair_attempts=0)

    result = manager.execute(_decision())

    assert result.status == "hedge_mismatch"
    assert result.buy_filled_qty == 0.1
    assert result.sell_filled_qty == 0.0
    assert result.residual_qty == 0.1


def test_cross_venue_order_manager_counts_real_fill_returned_by_adapter():
    hl = FakeAdapter(place_fill=Fill(0.1))
    paradex = FakeAdapter(fills=[Fill(0.1)])
    manager = CrossVenueOrderManager({"hl": hl, "paradex": paradex}, max_residual_qty=0.01)

    result = manager.execute(_decision())

    assert result.status == "filled"
    assert result.buy_filled_qty == 0.1
    assert result.sell_filled_qty == 0.1
