from common.cross_venue_models import (
    CrossVenuePosition,
    CrossVenueSnapshot,
    VenuePosition,
    VenueQuote,
)
from strategies.cross_exchange_arb import CrossExchangeArbStrategy


def _snapshot(hl_bid=100.0, hl_ask=101.0, pdx_bid=103.0, pdx_ask=104.0):
    return CrossVenueSnapshot.from_quotes(
        asset="SOL",
        hl=VenueQuote(venue="hl", instrument="SOL-PERP", bid=hl_bid, ask=hl_ask, mid=(hl_bid + hl_ask) / 2, timestamp_ms=1),
        paradex=VenueQuote(venue="paradex", instrument="SOL-USD-PERP", bid=pdx_bid, ask=pdx_ask, mid=(pdx_bid + pdx_ask) / 2, timestamp_ms=1),
    )


def _position(hl_qty=0.0, pdx_qty=0.0):
    return CrossVenuePosition(
        hl=VenuePosition(venue="hl", instrument="SOL-PERP", qty=hl_qty, avg_entry_price=0.0),
        paradex=VenuePosition(venue="paradex", instrument="SOL-USD-PERP", qty=pdx_qty, avg_entry_price=0.0),
    )


def test_cross_exchange_arb_buys_hl_and_sells_paradex_when_edge_clears_threshold():
    strat = CrossExchangeArbStrategy(
        min_profit_bps=5.0,
        slippage_buffer_bps=0.0,
        hl_taker_fee_bps=0.0,
        paradex_taker_fee_bps=0.0,
        base_size=0.1,
        max_size=0.25,
    )

    decisions = strat.on_tick(_snapshot(hl_ask=100.0, pdx_bid=101.0), _position())

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.buy_venue == "hl"
    assert decision.sell_venue == "paradex"
    assert decision.buy_instrument == "SOL-PERP"
    assert decision.sell_instrument == "SOL-USD-PERP"
    assert decision.size == 0.1
    assert decision.buy_price == 100.0
    assert decision.sell_price == 101.0
    assert decision.expected_edge_bps > 5.0


def test_cross_exchange_arb_buys_paradex_and_sells_hl_when_reverse_edge_clears_threshold():
    strat = CrossExchangeArbStrategy(
        min_profit_bps=5.0,
        slippage_buffer_bps=0.0,
        hl_taker_fee_bps=0.0,
        paradex_taker_fee_bps=0.0,
        base_size=0.1,
    )

    decisions = strat.on_tick(_snapshot(hl_bid=101.0, hl_ask=102.0, pdx_bid=99.0, pdx_ask=100.0), _position())

    assert len(decisions) == 1
    assert decisions[0].buy_venue == "paradex"
    assert decisions[0].sell_venue == "hl"


def test_cross_exchange_arb_blocks_when_net_exposure_already_too_large():
    strat = CrossExchangeArbStrategy(
        min_profit_bps=5.0,
        slippage_buffer_bps=0.0,
        hl_taker_fee_bps=0.0,
        paradex_taker_fee_bps=0.0,
        base_size=0.1,
        max_net_exposure=0.05,
    )

    decisions = strat.on_tick(_snapshot(hl_ask=100.0, pdx_bid=101.0), _position(hl_qty=0.1, pdx_qty=0.0))

    assert decisions == []


def test_cross_exchange_arb_includes_fees_and_slippage_in_threshold():
    strat = CrossExchangeArbStrategy(
        min_profit_bps=5.0,
        slippage_buffer_bps=3.0,
        hl_taker_fee_bps=4.0,
        paradex_taker_fee_bps=3.0,
        base_size=0.1,
    )

    # Gross edge is about 10 bps, below all-in 15 bps threshold.
    decisions = strat.on_tick(_snapshot(hl_ask=100.0, pdx_bid=100.1), _position())

    assert decisions == []
