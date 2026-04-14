from __future__ import annotations

from common.models import MarketSnapshot
from sdk.strategy_sdk.base import StrategyContext
from strategies.avellaneda_mm import AvellanedaStoikovMM


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        instrument="SOL-USD-PERP",
        mid_price=100.0,
        bid=99.95,
        ask=100.05,
        spread_bps=10.0,
        timestamp_ms=1,
    )


def _context(position_qty: float = 0.0) -> StrategyContext:
    return StrategyContext(
        snapshot=_snapshot(),
        position_qty=position_qty,
        position_notional=0.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        reduce_only=False,
        safe_mode=False,
        round_number=1,
        meta={},
    )


def test_default_strategy_uses_gtc_quotes():
    strat = AvellanedaStoikovMM(strategy_id="avellaneda_mm")
    orders = strat.on_tick(_snapshot(), _context())
    assert len(orders) == 2
    assert {o.order_type for o in orders} == {"Gtc"}
    assert orders[0].limit_price < _snapshot().ask
    assert orders[1].limit_price > _snapshot().bid


def test_ioc_mode_crosses_top_of_book_to_seek_fills():
    strat = AvellanedaStoikovMM(
        strategy_id="avellaneda_mm",
        order_type="Ioc",
        ioc_cross_bps=2.0,
        min_spread_bps=1.0,
        max_spread_bps=5.0,
    )
    orders = strat.on_tick(_snapshot(), _context())
    buy = next(o for o in orders if o.side == "buy")
    sell = next(o for o in orders if o.side == "sell")
    assert buy.order_type == "Ioc"
    assert sell.order_type == "Ioc"
    assert buy.limit_price >= _snapshot().ask
    assert sell.limit_price <= _snapshot().bid


def test_reduce_only_order_type_follows_configured_order_type():
    strat = AvellanedaStoikovMM(strategy_id="avellaneda_mm", order_type="Ioc", ioc_cross_bps=1.0)
    ctx = StrategyContext(
        snapshot=_snapshot(),
        position_qty=-0.2,
        position_notional=-20.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        reduce_only=True,
        safe_mode=False,
        round_number=1,
        meta={},
    )
    orders = strat.on_tick(_snapshot(), ctx)
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].order_type == "Ioc"
    assert orders[0].limit_price >= _snapshot().ask
