"""Simple two-leg cross-exchange arbitrage strategy."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from common.cross_venue_models import CrossVenueDecision, CrossVenuePosition, CrossVenueSnapshot
from sdk.strategy_sdk.cross_venue_base import BaseCrossVenueStrategy


class CrossExchangeArbStrategy(BaseCrossVenueStrategy):
    """Detects top-of-book HL ↔ Paradex spread opportunities.

    MVP scope: emits at most one paired IOC decision per tick and blocks new
    entries when existing net cross-venue exposure exceeds the configured cap.
    """

    def __init__(
        self,
        strategy_id: str = "cross_exchange_arb",
        min_profit_bps: float = 5.0,
        slippage_buffer_bps: float = 3.0,
        hl_taker_fee_bps: float = 4.0,
        paradex_taker_fee_bps: float = 3.0,
        base_size: float = 0.1,
        max_size: float = 0.25,
        max_net_exposure: float = 0.05,
        order_type: str = "Ioc",
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)
        self.min_profit_bps = float(min_profit_bps)
        self.slippage_buffer_bps = float(slippage_buffer_bps)
        self.hl_taker_fee_bps = float(hl_taker_fee_bps)
        self.paradex_taker_fee_bps = float(paradex_taker_fee_bps)
        self.base_size = max(0.0, float(base_size))
        self.max_size = max(0.0, float(max_size))
        self.max_net_exposure = max(0.0, float(max_net_exposure))
        self.order_type = order_type or "Ioc"

    @property
    def all_in_threshold_bps(self) -> float:
        return self.min_profit_bps + self.slippage_buffer_bps + self.hl_taker_fee_bps + self.paradex_taker_fee_bps

    def on_tick(
        self,
        snapshot: CrossVenueSnapshot,
        position: CrossVenuePosition,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[CrossVenueDecision]:
        if not snapshot.hl.valid or not snapshot.paradex.valid:
            return []
        if abs(position.net_qty) > self.max_net_exposure:
            return []

        size = min(self.base_size, self.max_size)
        if size <= 0:
            return []

        threshold = self.all_in_threshold_bps
        candidates: List[CrossVenueDecision] = []

        if snapshot.hl_buy_paradex_sell_edge_bps >= threshold:
            candidates.append(CrossVenueDecision(
                pair_id=str(uuid.uuid4()),
                buy_venue="hl",
                sell_venue="paradex",
                buy_instrument=snapshot.hl.instrument,
                sell_instrument=snapshot.paradex.instrument,
                size=size,
                buy_price=snapshot.hl.ask,
                sell_price=snapshot.paradex.bid,
                order_type=self.order_type,
                expected_edge_bps=snapshot.hl_buy_paradex_sell_edge_bps,
                meta={"direction": "buy_hl_sell_paradex", "threshold_bps": threshold},
            ))

        if snapshot.paradex_buy_hl_sell_edge_bps >= threshold:
            candidates.append(CrossVenueDecision(
                pair_id=str(uuid.uuid4()),
                buy_venue="paradex",
                sell_venue="hl",
                buy_instrument=snapshot.paradex.instrument,
                sell_instrument=snapshot.hl.instrument,
                size=size,
                buy_price=snapshot.paradex.ask,
                sell_price=snapshot.hl.bid,
                order_type=self.order_type,
                expected_edge_bps=snapshot.paradex_buy_hl_sell_edge_bps,
                meta={"direction": "buy_paradex_sell_hl", "threshold_bps": threshold},
            ))

        if not candidates:
            return []
        return [max(candidates, key=lambda d: d.expected_edge_bps)]
