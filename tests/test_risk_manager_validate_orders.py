from decimal import Decimal

from parent.position_tracker import PositionTracker
from parent.risk_manager import RiskLimits, RiskManager



def _rm(max_position_qty: str = "0.25", max_order_size: str = "1.0") -> RiskManager:
    return RiskManager(
        limits=RiskLimits(
            max_position_qty=Decimal(max_position_qty),
            max_notional_usd=Decimal("1000"),
            max_order_size=Decimal(max_order_size),
            max_daily_drawdown_pct=Decimal("10"),
            max_leverage=Decimal("2"),
            tvl=Decimal("3000"),
            reserve_factor_pct=Decimal("10"),
        )
    )



def test_validate_orders_rejects_cumulative_overshoot_from_flat():
    rm = _rm(max_position_qty="0.25")
    positions = PositionTracker()
    orders = [
        {"side": "buy", "size": 0.25, "quantity": 0.25, "limit_price": 100},
        {"side": "buy", "size": 0.25, "quantity": 0.25, "limit_price": 100},
    ]

    valid = rm.validate_orders(orders, "SOL-USD-PERP", positions)

    assert len(valid) == 1
    assert valid[0]["side"] == "buy"
    assert Decimal(str(valid[0]["size"])) == Decimal("0.25")



def test_validate_orders_rejects_buy_that_would_push_existing_long_past_cap():
    rm = _rm(max_position_qty="0.25")
    positions = PositionTracker()
    positions.apply_fill("mm", "SOL-USD-PERP", "buy", Decimal("0.13"), Decimal("84.0"))
    orders = [
        {"side": "buy", "size": 0.25, "quantity": 0.25, "limit_price": 100},
        {"side": "sell", "size": 0.25, "quantity": 0.25, "limit_price": 100},
    ]

    valid = rm.validate_orders(orders, "SOL-USD-PERP", positions)

    assert [o["side"] for o in valid] == ["sell"]



def test_validate_orders_allows_reduce_only_order_while_at_limit():
    rm = _rm(max_position_qty="0.25")
    positions = PositionTracker()
    positions.apply_fill("mm", "SOL-USD-PERP", "buy", Decimal("0.25"), Decimal("84.0"))
    orders = [
        {"side": "sell", "size": 0.12, "quantity": 0.12, "limit_price": 100},
    ]

    valid = rm.validate_orders(orders, "SOL-USD-PERP", positions)

    assert len(valid) == 1
    assert valid[0]["side"] == "sell"
