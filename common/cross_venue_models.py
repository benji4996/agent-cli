"""Models for cross-venue arbitrage between two execution venues."""
from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, Field


class CrossVenueInstrument(BaseModel):
    asset: str
    hl_instrument: str
    paradex_instrument: str


class VenueQuote(BaseModel):
    venue: str
    instrument: str
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    timestamp_ms: int = 0

    @property
    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.mid > 0


class CrossVenueSnapshot(BaseModel):
    asset: str
    hl: VenueQuote
    paradex: VenueQuote
    hl_buy_paradex_sell_edge_bps: float = 0.0
    paradex_buy_hl_sell_edge_bps: float = 0.0
    timestamp_ms: int = 0

    @classmethod
    def from_quotes(cls, *, asset: str, hl: VenueQuote, paradex: VenueQuote) -> "CrossVenueSnapshot":
        mid_ref = _mid_ref(hl, paradex)
        hl_buy_pdx_sell = ((paradex.bid - hl.ask) / mid_ref * 10_000) if mid_ref > 0 and hl.ask > 0 and paradex.bid > 0 else 0.0
        pdx_buy_hl_sell = ((hl.bid - paradex.ask) / mid_ref * 10_000) if mid_ref > 0 and paradex.ask > 0 and hl.bid > 0 else 0.0
        return cls(
            asset=asset,
            hl=hl,
            paradex=paradex,
            hl_buy_paradex_sell_edge_bps=hl_buy_pdx_sell,
            paradex_buy_hl_sell_edge_bps=pdx_buy_hl_sell,
            timestamp_ms=max(hl.timestamp_ms, paradex.timestamp_ms, int(time.time() * 1000)),
        )


class VenuePosition(BaseModel):
    venue: str
    instrument: str
    qty: float = 0.0
    avg_entry_price: float = 0.0


class CrossVenuePosition(BaseModel):
    hl: VenuePosition
    paradex: VenuePosition

    @property
    def net_qty(self) -> float:
        return self.hl.qty + self.paradex.qty

    @property
    def residual_qty(self) -> float:
        return abs(self.net_qty)


class CrossVenueDecision(BaseModel):
    pair_id: str
    buy_venue: str
    sell_venue: str
    buy_instrument: str
    sell_instrument: str
    size: float
    buy_price: float
    sell_price: float
    order_type: str = "Ioc"
    expected_edge_bps: float = 0.0
    meta: dict = Field(default_factory=dict)


class CrossVenueExecutionResult(BaseModel):
    pair_id: str
    buy_ok: bool = False
    sell_ok: bool = False
    buy_filled_qty: float = 0.0
    sell_filled_qty: float = 0.0
    residual_qty: float = 0.0
    status: str = "unknown"
    error: Optional[str] = None


def _mid_ref(hl: VenueQuote, paradex: VenueQuote) -> float:
    mids = [q.mid for q in (hl, paradex) if q.mid > 0]
    if mids:
        return sum(mids) / len(mids)
    prices = [hl.bid, hl.ask, paradex.bid, paradex.ask]
    positive = [p for p in prices if p > 0]
    return sum(positive) / len(positive) if positive else 0.0
