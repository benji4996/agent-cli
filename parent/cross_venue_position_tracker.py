"""Venue-specific position tracking for cross-venue arbitrage."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from common.cross_venue_models import CrossVenuePosition, VenuePosition


class CrossVenuePositionTracker:
    def __init__(self, *, asset: str, hl_instrument: str, paradex_instrument: str):
        self.asset = asset
        self.hl_instrument = hl_instrument
        self.paradex_instrument = paradex_instrument
        self.position = CrossVenuePosition(
            hl=VenuePosition(venue="hl", instrument=hl_instrument),
            paradex=VenuePosition(venue="paradex", instrument=paradex_instrument),
        )

    def sync_from_adapters(self, hl_adapter, paradex_adapter) -> CrossVenuePosition:
        hl_state = hl_adapter.get_account_state() or {}
        paradex_state = paradex_adapter.get_account_state() or {}
        hl_pos = self._extract_hl_position(hl_state.get("positions") or [])
        pdx_pos = self._extract_paradex_position(paradex_state.get("positions") or [])
        self.position = CrossVenuePosition(hl=hl_pos, paradex=pdx_pos)
        return self.position

    def get_position(self) -> CrossVenuePosition:
        return self.position

    def is_hedged(self, max_residual_qty: float) -> bool:
        return abs(self.position.net_qty) <= float(max_residual_qty)

    def _extract_hl_position(self, positions: Iterable[Dict[str, Any]]) -> VenuePosition:
        target = self.hl_instrument.upper()
        asset = self.asset.upper()
        for raw in positions:
            symbol = str(raw.get("symbol") or raw.get("coin") or raw.get("instrument") or raw.get("market") or "")
            if symbol.upper() not in {target, asset}:
                continue
            qty = _float(raw.get("szi", raw.get("size", raw.get("qty", raw.get("position", 0.0)))))
            avg = _float(raw.get("entryPx", raw.get("entry_price", raw.get("avg_entry_price", raw.get("average_entry_price", 0.0)))))
            return VenuePosition(venue="hl", instrument=self.hl_instrument, qty=qty, avg_entry_price=avg)
        return VenuePosition(venue="hl", instrument=self.hl_instrument)

    def _extract_paradex_position(self, positions: Iterable[Dict[str, Any]]) -> VenuePosition:
        target = self.paradex_instrument.upper()
        for raw in positions:
            symbol = str(raw.get("market") or raw.get("symbol") or raw.get("instrument") or "")
            if symbol.upper() != target:
                continue
            qty = _float(raw.get("size", raw.get("qty", 0.0)))
            side = str(raw.get("side") or "").upper()
            if side == "SHORT" and qty > 0:
                qty = -qty
            avg = _float(raw.get("average_entry_price", raw.get("avg_entry_price", raw.get("entry_price", 0.0))))
            return VenuePosition(venue="paradex", instrument=self.paradex_instrument, qty=qty, avg_entry_price=avg)
        return VenuePosition(venue="paradex", instrument=self.paradex_instrument)


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
