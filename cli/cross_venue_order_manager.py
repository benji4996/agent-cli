"""Paired order execution for cross-venue arbitrage."""
from __future__ import annotations

import logging
import time
from typing import Dict

from common.cross_venue_models import CrossVenueDecision, CrossVenueExecutionResult

log = logging.getLogger("cross_venue_order_manager")


class CrossVenueOrderManager:
    """Executes a paired buy/sell decision across two venue adapters.

    MVP policy: IOC-style paired submission, fill verification from exchange fill
    collectors, and stop/flag on hedge mismatch. It deliberately does not treat
    order acknowledgements as fills.
    """

    def __init__(
        self,
        adapters: Dict[str, object],
        *,
        dry_run: bool = False,
        max_residual_qty: float = 0.03,
        hedge_repair_attempts: int = 0,
        fill_wait_s: float = 0.5,
    ):
        self.adapters = adapters
        self.dry_run = dry_run
        self.max_residual_qty = max(0.0, float(max_residual_qty))
        self.hedge_repair_attempts = max(0, int(hedge_repair_attempts))
        self.fill_wait_s = max(0.0, float(fill_wait_s))

    def execute(self, decision: CrossVenueDecision) -> CrossVenueExecutionResult:
        if self.dry_run:
            log.info(
                "[DRY RUN] pair=%s BUY %s %.6f %s @ %.4f / SELL %s %.6f %s @ %.4f edge=%.2fbps",
                decision.pair_id,
                decision.buy_venue,
                decision.size,
                decision.buy_instrument,
                decision.buy_price,
                decision.sell_venue,
                decision.size,
                decision.sell_instrument,
                decision.sell_price,
                decision.expected_edge_bps,
            )
            return CrossVenueExecutionResult(
                pair_id=decision.pair_id,
                buy_ok=True,
                sell_ok=True,
                status="dry_run",
            )

        buy_adapter = self.adapters[decision.buy_venue]
        sell_adapter = self.adapters[decision.sell_venue]
        result = CrossVenueExecutionResult(pair_id=decision.pair_id)

        try:
            self._cancel_stale(buy_adapter, decision.buy_instrument)
            self._cancel_stale(sell_adapter, decision.sell_instrument)
            buy_fill = buy_adapter.place_order(
                instrument=decision.buy_instrument,
                side="buy",
                size=decision.size,
                price=decision.buy_price,
                tif=decision.order_type,
            )
            result.buy_ok = True
            sell_fill = sell_adapter.place_order(
                instrument=decision.sell_instrument,
                side="sell",
                size=decision.size,
                price=decision.sell_price,
                tif=decision.order_type,
            )
            result.sell_ok = True
        except Exception as e:
            result.status = "submit_error"
            result.error = f"{type(e).__name__}: {e}"
            log.exception("Cross-venue pair submit failed: %s", decision.pair_id)
            return result

        if self.fill_wait_s:
            time.sleep(self.fill_wait_s)

        result.buy_filled_qty = _fill_qty(locals().get("buy_fill")) + self._collect_qty(buy_adapter, decision.buy_instrument)
        result.sell_filled_qty = _fill_qty(locals().get("sell_fill")) + self._collect_qty(sell_adapter, decision.sell_instrument)
        result.residual_qty = abs(result.buy_filled_qty - result.sell_filled_qty)
        if result.residual_qty <= self.max_residual_qty:
            result.status = "filled"
        else:
            result.status = "hedge_mismatch"
            log.error(
                "Cross-venue hedge mismatch pair=%s buy_filled=%.6f sell_filled=%.6f residual=%.6f",
                decision.pair_id,
                result.buy_filled_qty,
                result.sell_filled_qty,
                result.residual_qty,
            )
        return result

    def _cancel_stale(self, adapter, instrument: str) -> None:
        get_open_orders = getattr(adapter, "get_open_orders", None)
        cancel_order = getattr(adapter, "cancel_order", None)
        if not callable(get_open_orders) or not callable(cancel_order):
            return
        for order in get_open_orders(instrument) or []:
            oid = order.get("oid") or order.get("id") or order.get("order_id") or order.get("client_id")
            if oid:
                cancel_order(instrument, oid)

    def _collect_qty(self, adapter, instrument: str) -> float:
        collector = getattr(adapter, "collect_new_fills", None)
        if not callable(collector):
            return 0.0
        qty = 0.0
        for fill in collector(instrument) or []:
            qty += _fill_qty(fill)
        return round(qty, 12)


def _fill_qty(fill) -> float:
    for attr in ("quantity", "qty", "size"):
        if hasattr(fill, attr):
            try:
                return float(getattr(fill, attr) or 0.0)
            except (TypeError, ValueError):
                return 0.0
    if isinstance(fill, dict):
        for key in ("quantity", "qty", "size"):
            if key in fill:
                try:
                    return float(fill.get(key) or 0.0)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0
