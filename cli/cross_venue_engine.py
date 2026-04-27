"""Cross-venue arbitrage engine for paired HL ↔ Paradex execution."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict

from common.cross_venue_models import CrossVenueSnapshot, VenueQuote
from parent.cross_venue_position_tracker import CrossVenuePositionTracker
from sdk.strategy_sdk.cross_venue_base import BaseCrossVenueStrategy

from cli.cross_venue_order_manager import CrossVenueOrderManager

log = logging.getLogger("cross_venue_engine")


class CrossVenueEngine:
    """Minimal dual-venue engine: fetch both books, decide, execute paired legs."""

    def __init__(
        self,
        *,
        hl_adapter,
        paradex_adapter,
        strategy: BaseCrossVenueStrategy,
        asset: str,
        hl_instrument: str,
        paradex_instrument: str,
        tick_interval: float = 2.0,
        dry_run: bool = True,
        data_dir: str = "data/cross-venue",
        max_residual_qty: float = 0.03,
        hedge_repair_attempts: int = 0,
    ):
        self.adapters = {"hl": hl_adapter, "paradex": paradex_adapter}
        self.strategy = strategy
        self.asset = asset
        self.hl_instrument = hl_instrument
        self.paradex_instrument = paradex_instrument
        self.tick_interval = max(0.0, float(tick_interval))
        self.dry_run = bool(dry_run)
        self.data_dir = data_dir
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.position_tracker = CrossVenuePositionTracker(
            asset=asset,
            hl_instrument=hl_instrument,
            paradex_instrument=paradex_instrument,
        )
        self.order_manager = CrossVenueOrderManager(
            self.adapters,
            dry_run=dry_run,
            max_residual_qty=max_residual_qty,
            hedge_repair_attempts=hedge_repair_attempts,
        )
        self.tick_count = 0
        self.safe_mode = False

    def run(self, max_ticks: int = 0) -> None:
        log.info(
            "Cross-venue engine started asset=%s hl=%s paradex=%s dry_run=%s tick=%.2fs",
            self.asset,
            self.hl_instrument,
            self.paradex_instrument,
            self.dry_run,
            self.tick_interval,
        )
        while True:
            if max_ticks and self.tick_count >= max_ticks:
                log.info("Reached max ticks (%d), stopping", max_ticks)
                break
            if self.safe_mode:
                log.error("Safe mode active, stopping cross-venue engine")
                break
            self._tick()
            if self.tick_interval > 0:
                time.sleep(self.tick_interval)

    def _tick(self) -> None:
        self.tick_count += 1
        snapshot = self._snapshot()
        position = self.position_tracker.sync_from_adapters(self.adapters["hl"], self.adapters["paradex"])
        decisions = self.strategy.on_tick(snapshot, position, context={"tick": self.tick_count})
        log.info(
            "T%d %s edge hl->pdx=%.2fbps pdx->hl=%.2fbps net=%.6f decisions=%d",
            self.tick_count,
            self.asset,
            snapshot.hl_buy_paradex_sell_edge_bps,
            snapshot.paradex_buy_hl_sell_edge_bps,
            position.net_qty,
            len(decisions),
        )
        for decision in decisions[:1]:
            result = self.order_manager.execute(decision)
            log.info("pair=%s status=%s residual=%.6f", result.pair_id, result.status, result.residual_qty)
            if result.status in {"hedge_mismatch", "submit_error"}:
                self.safe_mode = True
                break

    def _snapshot(self) -> CrossVenueSnapshot:
        hl_snap = self.adapters["hl"].get_snapshot(self.hl_instrument)
        pdx_snap = self.adapters["paradex"].get_snapshot(self.paradex_instrument)
        return CrossVenueSnapshot.from_quotes(
            asset=self.asset,
            hl=VenueQuote(
                venue="hl",
                instrument=self.hl_instrument,
                bid=float(hl_snap.bid),
                ask=float(hl_snap.ask),
                mid=float(hl_snap.mid_price),
                timestamp_ms=int(hl_snap.timestamp_ms),
            ),
            paradex=VenueQuote(
                venue="paradex",
                instrument=self.paradex_instrument,
                bid=float(pdx_snap.bid),
                ask=float(pdx_snap.ask),
                mid=float(pdx_snap.mid_price),
                timestamp_ms=int(pdx_snap.timestamp_ms),
            ),
        )
