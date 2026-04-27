from parent.cross_venue_position_tracker import CrossVenuePositionTracker


class FakeAdapter:
    def __init__(self, positions):
        self._positions = positions

    def get_account_state(self):
        return {"positions": self._positions}


def test_cross_venue_position_tracker_syncs_signed_long_short_positions():
    hl = FakeAdapter([
        {"symbol": "SOL-PERP", "szi": "0.25", "entryPx": "100.0"},
    ])
    paradex = FakeAdapter([
        {"market": "SOL-USD-PERP", "side": "SHORT", "size": "0.20", "average_entry_price": "101.0"},
    ])
    tracker = CrossVenuePositionTracker(
        asset="SOL",
        hl_instrument="SOL-PERP",
        paradex_instrument="SOL-USD-PERP",
    )

    position = tracker.sync_from_adapters(hl, paradex)

    assert position.hl.qty == 0.25
    assert position.hl.avg_entry_price == 100.0
    assert position.paradex.qty == -0.20
    assert position.paradex.avg_entry_price == 101.0
    assert round(position.net_qty, 6) == 0.05


def test_cross_venue_position_tracker_reports_hedge_residual():
    tracker = CrossVenuePositionTracker(
        asset="SOL",
        hl_instrument="SOL-PERP",
        paradex_instrument="SOL-USD-PERP",
    )
    position = tracker.sync_from_adapters(
        FakeAdapter([{"symbol": "SOL-PERP", "szi": "0.10"}]),
        FakeAdapter([{"market": "SOL-USD-PERP", "side": "SHORT", "size": "0.03"}]),
    )

    assert round(position.net_qty, 6) == 0.07
    assert tracker.is_hedged(max_residual_qty=0.05) is False
    assert tracker.is_hedged(max_residual_qty=0.10) is True
