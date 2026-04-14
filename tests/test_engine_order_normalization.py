from __future__ import annotations

from decimal import Decimal

from cli.engine import TradingEngine
from common.models import MarketSnapshot, StrategyDecision
from parent.risk_manager import RiskLimits
from sdk.strategy_sdk.base import BaseStrategy


class DummyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(strategy_id="dummy")

    def on_tick(self, snapshot, context=None):
        return [
            StrategyDecision(
                action="place_order",
                instrument="SOL-USD-PERP",
                side="buy",
                size=0.04,
                limit_price=83.67,
                order_type="Alo",
            )
        ]


class DummyVenue:
    def get_snapshot(self, instrument: str):
        return MarketSnapshot(
            instrument=instrument,
            mid_price=83.67,
            bid=83.66,
            ask=83.68,
            spread_bps=2.0,
            timestamp_ms=1_000,
        )

    def normalize_order(self, instrument: str, side: str, size: float, price: float, tif: str):
        return {"size": 0.12, "price": price}


class RecordingOrderManager:
    def __init__(self):
        self.decisions = None
        self.stats = {"total_placed": 0, "total_filled": 0}

    def update(self, decisions, snapshot):
        self.decisions = list(decisions)
        return []


def test_engine_normalizes_orders_before_risk_validation():
    venue = DummyVenue()
    engine = TradingEngine(
        hl=venue,
        strategy=DummyStrategy(),
        instrument="SOL-USD-PERP",
        tick_interval=0.0,
        dry_run=True,
        data_dir="data/test-engine-order-normalization",
        risk_limits=RiskLimits(
            max_position_qty=Decimal("1.0"),
            max_notional_usd=Decimal("1000"),
            max_order_size=Decimal("0.10"),
            max_daily_drawdown_pct=Decimal("10"),
            max_leverage=Decimal("10"),
            tvl=Decimal("1000"),
        ),
    )
    recorder = RecordingOrderManager()
    engine.order_manager = recorder

    engine._tick()

    assert recorder.decisions == []
