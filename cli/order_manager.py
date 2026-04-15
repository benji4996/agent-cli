"""Order lifecycle management — place, track, cancel."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from common.models import MarketSnapshot, StrategyDecision
from execution.parent_order import ParentOrder
from execution.routing import OrderRouter
from execution.twap import TWAPExecutor, ChildSlice
from parent.hl_proxy import HLFill

if TYPE_CHECKING:
    from cli.hl_adapter import DirectHLProxy, DirectMockProxy
    from common.venue_adapter import VenueAdapter

log = logging.getLogger("order_manager")


class OrderManager:
    """Manages order lifecycle each tick: cancel stale -> place new -> collect fills.

    Respects each StrategyDecision's order_type field:
    - "Gtc" (default): rests on book as passive limit order (MM strategies)
    - "Ioc": crosses spread for immediate fill (directional strategies)
    - "Alo": maker-only, rejects if it would cross

    Each tick cancels stale orders before placing fresh quotes.
    Supports TWAP execution for large orders via execution_algo meta field.
    """

    def __init__(
        self,
        hl,  # VenueAdapter (or DirectHLProxy | DirectMockProxy for backwards compat)
        instrument: str = "ETH-PERP",
        dry_run: bool = False,
        builder: dict = None,
        router: Optional[OrderRouter] = None,
        maker_refresh_interval_s: float = 0.0,
    ):
        self.hl = hl
        self.instrument = instrument
        self.dry_run = dry_run
        self._builder = builder
        self._router = router
        self._total_placed = 0
        self._total_filled = 0
        self._twap = TWAPExecutor()
        self._maker_refresh_interval_ms = max(0, int(float(maker_refresh_interval_s or 0.0) * 1000))
        self._last_maker_quote_ms = 0

    def update(
        self,
        decisions: List[StrategyDecision],
        snapshot: MarketSnapshot,
    ) -> List[HLFill]:
        """Full tick cycle: collect fills -> cancel open orders -> place new -> collect fills."""
        fills: List[HLFill] = []

        fills.extend(self._collect_exchange_fills())

        passive_only = self._is_passive_only(decisions)
        open_orders: List[Dict] = []
        if not self.dry_run and passive_only and self._maker_refresh_interval_ms > 0:
            open_orders = list(self.hl.get_open_orders(self.instrument) or [])

        # 1. Cancel any lingering open orders (safety net for IOC leftovers)
        if self._should_refresh_maker_quotes(decisions, snapshot, open_orders):
            if open_orders:
                self._cancel_orders(open_orders)
            else:
                self.cancel_all()
        elif passive_only:
            log.info(
                "Preserving %d resting maker quotes for %s (refresh interval %.1fs not reached)",
                len(open_orders),
                self.instrument,
                self._maker_refresh_interval_ms / 1000.0,
            )
            exchange_fills = self._collect_exchange_fills()
            fills.extend(exchange_fills)
            self._total_filled += len(exchange_fills)
            return fills
        else:
            self.cancel_all()

        # 2. Process active TWAP orders
        twap_slices = self._twap.on_tick(snapshot)
        for s in twap_slices:
            fill = self._execute_child_slice(s)
            if fill is not None:
                fills.append(fill)
                self._twap.record_fill(
                    s.parent_order_id, fill.size, fill.price,
                    snapshot.timestamp_ms,
                )

        # 3. Place new orders from strategy decisions
        for d in decisions:
            if d.action != "place_order" or d.size <= 0 or d.limit_price <= 0:
                continue

            # Route to TWAP if execution_algo says so
            if d.meta.get("execution_algo") == "twap":
                parent = ParentOrder(
                    instrument=d.instrument or self.instrument,
                    side=d.side,
                    target_qty=d.size,
                    algo="twap",
                    duration_ticks=d.meta.get("twap_duration_ticks", 5),
                    urgency=d.meta.get("twap_urgency", 0.7),
                    created_at_ms=snapshot.timestamp_ms,
                )
                self._twap.submit(parent)
                log.info("TWAP submitted: %s %s %.6f over %d ticks",
                         d.side.upper(), parent.instrument,
                         parent.target_qty, parent.duration_ticks)
                self._total_placed += 1
                continue

            # Determine TIF via router (if available) or use decision's order_type
            tif = d.order_type
            if self._router is not None:
                urgency = d.meta.get("urgency", 0.5)
                tif = self._router.route(d, snapshot, urgency=urgency)

            if self.dry_run:
                log.info("[DRY RUN] %s %s %.6f @ %.4f (tif=%s)",
                         d.side.upper(), d.instrument or self.instrument,
                         d.size, d.limit_price, tif)
                self._total_placed += 1
                continue

            fill = self.hl.place_order(
                instrument=d.instrument or self.instrument,
                side=d.side,
                size=d.size,
                price=d.limit_price,
                tif=tif,
                builder=self._builder,
            )
            self._total_placed += 1

            # Record routing stats
            if self._router is not None:
                if tif == "Alo":
                    size_usd = d.size * d.limit_price
                    self._router.stats.record_alo_attempt(
                        success=fill is not None, size_usd=size_usd,
                    )
                else:
                    self._router.stats.record_order(tif)

            if fill is not None:
                fills.append(fill)
                self._total_filled += 1

        if passive_only and decisions:
            self._last_maker_quote_ms = snapshot.timestamp_ms

        exchange_fills = self._collect_exchange_fills()
        fills.extend(exchange_fills)
        self._total_filled += len(exchange_fills)

        return fills

    def _execute_child_slice(self, s: ChildSlice) -> HLFill | None:
        """Execute a single TWAP child slice as an IOC order."""
        if self.dry_run:
            log.info("[DRY RUN TWAP] %s %s %.6f @ %.4f",
                     s.side.upper(), s.instrument, s.size, s.price)
            self._total_placed += 1
            return None

        fill = self.hl.place_order(
            instrument=s.instrument,
            side=s.side,
            size=s.size,
            price=s.price,
            tif="Ioc",
            builder=self._builder,
        )
        self._total_placed += 1
        if fill is not None:
            self._total_filled += 1
        return fill

    def _collect_exchange_fills(self) -> List[HLFill]:
        if self.dry_run:
            return []
        collector = getattr(self.hl, "collect_new_fills", None)
        if not callable(collector):
            return []
        try:
            return list(collector(self.instrument) or [])
        except Exception as e:
            log.warning("Failed to collect exchange fills: %s", e)
            return []

    def _is_passive_only(self, decisions: List[StrategyDecision]) -> bool:
        active = [d for d in decisions if d.action == "place_order" and d.size > 0 and d.limit_price > 0]
        if not active:
            return False
        return all(str(d.order_type or "").lower() in {"gtc", "alo"} for d in active)

    def _should_refresh_maker_quotes(
        self,
        decisions: List[StrategyDecision],
        snapshot: MarketSnapshot,
        open_orders: List[Dict],
    ) -> bool:
        if not self._is_passive_only(decisions) or self._maker_refresh_interval_ms <= 0:
            return True

        active_decisions = [d for d in decisions if d.action == "place_order" and d.size > 0 and d.limit_price > 0]
        if not active_decisions:
            return False
        if not open_orders:
            return True
        if len(open_orders) != len(active_decisions):
            return True

        desired_sides = {str(d.side).lower() for d in active_decisions}
        open_sides = {str(order.get("side") or "").lower() for order in open_orders}
        if desired_sides != open_sides:
            return True

        if self._last_maker_quote_ms <= 0:
            return True

        return (snapshot.timestamp_ms - self._last_maker_quote_ms) >= self._maker_refresh_interval_ms

    def _cancel_orders(self, open_orders: List[Dict]) -> int:
        if self.dry_run:
            return 0
        cancelled = 0
        for order in open_orders:
            oid = (
                order.get("oid")
                or order.get("id")
                or order.get("order_id")
                or order.get("client_id")
                or ""
            )
            if oid and self.hl.cancel_order(self.instrument, oid):
                cancelled += 1
        if cancelled:
            log.info("Cancelled %d open orders", cancelled)
        return cancelled

    def cancel_all(self) -> int:
        """Cancel all open orders for the instrument."""
        if self.dry_run:
            return 0
        open_orders = self.hl.get_open_orders(self.instrument)
        return self._cancel_orders(open_orders)

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_placed": self._total_placed,
            "total_filled": self._total_filled,
        }
