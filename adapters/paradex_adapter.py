"""Paradex VenueAdapter — thin bridge around ParadexProxy.

Keeps SDK/auth/reconciliation details in parent/paradex_proxy.py and exposes the
repo's venue-agnostic adapter interface.
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Dict, List, Optional

import httpx

from common.models import MarketSnapshot
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities
from parent.paradex_proxy import ParadexFill, ParadexProxy

log = logging.getLogger("adapters.paradex")


def _paradex_fill_to_fill(fill: ParadexFill) -> Fill:
    return Fill(
        oid=fill.oid,
        instrument=fill.instrument,
        side=fill.side,
        price=float(fill.price),
        quantity=float(fill.quantity),
        timestamp_ms=fill.timestamp_ms,
        fee=float(fill.fee),
    )


class ParadexVenueAdapter(VenueAdapter):
    """VenueAdapter implementation backed by ParadexProxy."""

    def __init__(
        self,
        proxy: ParadexProxy,
        *,
        min_notional_mode: str = "strict",
        auto_bump_buffer_pct: float = 0.0,
        passive_min_notional_mode: str = "strict",
        reduce_only_min_notional_mode: str = "auto_bump",
    ):
        self._proxy = proxy
        self._seen_fill_ids: set[str] = set()
        self._fills_initialized = False
        self._min_notional_mode = (min_notional_mode or "strict").strip().lower()
        self._auto_bump_buffer_pct = max(0.0, float(auto_bump_buffer_pct or 0.0))
        self._passive_min_notional_mode = (passive_min_notional_mode or "strict").strip().lower()
        self._reduce_only_min_notional_mode = (reduce_only_min_notional_mode or "auto_bump").strip().lower()

    def connect(self, private_key: str, testnet: bool = True) -> None:
        self._proxy.connect()

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            supports_alo=True,
            supports_trigger_orders=False,
            supports_builder_fee=False,
            supports_cross_margin=False,
        )

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        try:
            market = self._proxy.get_market_metadata(instrument)
            summary = self._proxy.get_market_summary(instrument)
        except (httpx.ReadTimeout, json.JSONDecodeError) as e:
            log.warning(
                "Paradex snapshot fetch failed for %s due to transient market-data error; skipping tick: %s",
                instrument,
                e,
            )
            return MarketSnapshot(instrument=instrument)
        merged = dict(market)
        merged.update(summary)
        bid = self._coerce_float(merged, "best_bid", "bid", "bid_price")
        ask = self._coerce_float(merged, "best_ask", "ask", "ask_price")
        mid = self._coerce_float(merged, "mark_price", "mid", "mid_price", "index_price", "last_price")
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        spread = ((ask - bid) / mid * 10000) if mid > 0 and bid > 0 and ask > 0 else 0.0
        return MarketSnapshot(
            instrument=instrument,
            mid_price=mid,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            timestamp_ms=int(time.time() * 1000),
            volume_24h=self._coerce_float(merged, "volume_24h", "turnover_24h", "quote_volume_24h"),
            open_interest=self._coerce_float(merged, "open_interest", "openInterest"),
        )

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        return self._proxy.fetch_candles(coin, interval, lookback_ms)

    def get_all_markets(self) -> list:
        return self._proxy.fetch_markets()

    def get_all_mids(self) -> Dict[str, str]:
        mids: Dict[str, str] = {}
        for market in self._proxy.fetch_markets():
            instrument = str(market.get("symbol") or market.get("market") or market.get("instrument") or "")
            if not instrument:
                continue
            mid = self._coerce_float(market, "mark_price", "mid", "mid_price", "index_price", "last_price")
            if mid <= 0:
                bid = self._coerce_float(market, "best_bid", "bid", "bid_price")
                ask = self._coerce_float(market, "best_ask", "ask", "ask_price")
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
            mids[instrument] = str(mid if mid > 0 else 0.0)
        return mids

    def normalize_order(self, instrument: str, side: str, size: float, price: float, tif: str = "Ioc") -> Dict[str, float]:
        metadata = self._proxy.get_market_metadata(instrument)
        quantized_price = self._quantize_price(price, metadata)
        quantized_size = self._quantize_size(size, metadata)
        if self._is_passive_tif(tif) and self._passive_min_notional_mode == "floor":
            quantized_size = self._floor_passive_size_to_min_notional(
                instrument=instrument,
                side=side,
                size=quantized_size,
                price=quantized_price,
                tif=tif,
                metadata=metadata,
            )
        return {"size": quantized_size, "price": quantized_price}

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
        reduce_only: bool = False,
    ) -> Optional[Fill]:
        normalized = self.normalize_order(instrument, side, size, price, tif)
        metadata = self._proxy.get_market_metadata(instrument)
        quantized_price = normalized["price"]
        quantized_size = normalized["size"]
        adjusted_size = self._enforce_min_notional(
            instrument=instrument,
            side=side,
            raw_size=max(0.0, float(size)),
            size=quantized_size,
            price=quantized_price,
            tif=tif,
            metadata=metadata,
            mode_override=self._reduce_only_min_notional_mode if reduce_only else None,
        )
        if adjusted_size is None:
            return None
        order = {
            "symbol": instrument,
            "side": side.upper(),
            "size": adjusted_size,
            "price": quantized_price,
            "time_in_force": tif.upper(),
            "reduce_only": bool(reduce_only),
        }
        if builder:
            log.debug("Ignoring builder fee payload for Paradex order: %s", builder)
        try:
            self._proxy.submit_order(order)
        except ValueError as e:
            message = str(e)
            if "SYSTEM_STATUS_CANCEL_ONLY" in message or "only cancel orders are allowed" in message:
                log.warning(
                    "Paradex entered cancel-only mode; skipping new %s %s order this tick",
                    side.upper(), instrument,
                )
                return None
            raise
        return None

    def collect_new_fills(self, instrument: str = "") -> List[Fill]:
        instrument_upper = instrument.upper() if instrument else ""
        fresh: List[Fill] = []
        raw_fills = self._proxy.fetch_fills()
        chronological = sorted(raw_fills, key=lambda fill: int(fill.get("created_at") or fill.get("timestamp") or 0))
        if not self._fills_initialized:
            for raw in chronological:
                fill_id = str(raw.get("id") or raw.get("fill_id") or "")
                if fill_id:
                    self._seen_fill_ids.add(fill_id)
            self._fills_initialized = True
            return []
        for raw in chronological:
            fill_id = str(raw.get("id") or raw.get("fill_id") or "")
            if not fill_id or fill_id in self._seen_fill_ids:
                continue
            symbol = str(raw.get("market") or raw.get("symbol") or raw.get("instrument") or "")
            if instrument_upper and symbol.upper() != instrument_upper:
                continue
            self._seen_fill_ids.add(fill_id)
            fresh.append(
                Fill(
                    oid=fill_id,
                    instrument=symbol,
                    side=str(raw.get("side") or "").lower(),
                    price=float(raw.get("price") or 0.0),
                    quantity=float(raw.get("size") or raw.get("qty") or raw.get("quantity") or 0.0),
                    timestamp_ms=int(raw.get("created_at") or raw.get("timestamp_ms") or raw.get("timestamp") or int(time.time() * 1000)),
                    fee=float(raw.get("fee") or 0.0),
                )
            )
        return fresh

    def cancel_order(self, instrument: str, oid: str) -> bool:
        try:
            result = self._proxy.cancel_order(oid)
        except ValueError as e:
            message = str(e)
            if "ORDER_ID_NOT_FOUND" in message or "could not find order id" in message:
                log.info("Paradex order %s already gone during cancel; treating as benign race", oid)
                return True
            raise
        return bool(result)

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        try:
            orders = self._proxy.fetch_orders()
        except httpx.ReadTimeout:
            log.warning("Paradex fetch_orders timed out; treating open-order snapshot as unavailable for this tick")
            return []
        if not instrument:
            return orders
        instrument_upper = instrument.upper()
        filtered: List[Dict] = []
        for order in orders:
            symbol = str(order.get("symbol") or order.get("market") or order.get("instrument") or "")
            if symbol.upper() == instrument_upper:
                filtered.append(order)
        return filtered

    def get_account_state(self) -> Dict:
        return self._proxy.get_account_state()

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None:
        log.info("Paradex leverage control not implemented yet; requested leverage=%s coin=%s cross=%s", leverage, coin, is_cross)

    def _floor_passive_size_to_min_notional(
        self,
        *,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str,
        metadata: Dict[str, object],
    ) -> float:
        if price <= 0 or size <= 0:
            return size
        min_notional = self._coerce_float(metadata, "min_notional", "min_trade_value")
        if min_notional <= 0:
            return size
        increment = self._coerce_float(metadata, "order_size_increment", "size_increment") or 0.0
        notional = size * price
        if notional >= min_notional:
            return size
        floored = self._ceil_to_increment(min_notional / price, increment)
        if floored <= 0:
            return size
        log.info(
            "Flooring passive Paradex quote to min_notional: %s %s %.6f -> %.6f @ %.6f tif=%s",
            side.upper(), instrument, size, floored, price, tif.upper(),
        )
        return floored

    def _enforce_min_notional(
        self,
        *,
        instrument: str,
        side: str,
        raw_size: float,
        size: float,
        price: float,
        tif: str,
        metadata: Dict[str, object],
        mode_override: Optional[str] = None,
    ) -> Optional[float]:
        mode = (mode_override or self._min_notional_mode or "strict").strip().lower()
        if price <= 0 or raw_size <= 0:
            log.warning(
                "Skipping Paradex order with non-positive price/size: %s %s raw_size=%.6f quantized_size=%.6f price=%.6f tif=%s mode=%s",
                side.upper(), instrument, raw_size, size, price, tif.upper(), mode,
            )
            return None
        min_notional = self._coerce_float(metadata, "min_notional", "min_trade_value")
        increment = self._coerce_float(metadata, "order_size_increment", "size_increment") or 0.0
        effective_size = size if size > 0 else 0.0
        if min_notional <= 0:
            if effective_size > 0:
                return effective_size
            log.warning(
                "Skipping Paradex order after size quantized to zero: %s %s raw_size=%.6f price=%.6f tif=%s mode=%s",
                side.upper(), instrument, raw_size, price, tif.upper(), mode,
            )
            return None
        notional = effective_size * price
        if effective_size > 0 and notional >= min_notional:
            return effective_size
        if mode == "auto_bump":
            target_notional = min_notional * (1.0 + self._auto_bump_buffer_pct / 100.0)
            required_size = target_notional / price
            bumped = self._ceil_to_increment(required_size, increment)
            bumped_notional = bumped * price
            log.info(
                "Auto-bumping Paradex order to satisfy min_notional: %s %s %.6f -> %.6f @ %.6f (%s %.4f -> %.4f)",
                side.upper(), instrument, effective_size or raw_size, bumped, price, "notional", notional, bumped_notional,
            )
            return bumped if bumped > 0 else None
        if effective_size <= 0:
            log.warning(
                "Skipping Paradex order after size quantized to zero: %s %s raw_size=%.6f price=%.6f tif=%s mode=%s",
                side.upper(), instrument, raw_size, price, tif.upper(), mode,
            )
            return None
        log.warning(
            "Skipping Paradex order below min_notional: %s %s size=%.6f price=%.6f tif=%s notional=%.4f min_notional=%.4f mode=%s",
            side.upper(), instrument, effective_size, price, tif.upper(), notional, min_notional, mode,
        )
        return None

    @staticmethod
    def _is_passive_tif(tif: str) -> bool:
        return str(tif or "").strip().lower() in {"alo", "gtc"}

    @staticmethod
    def _coerce_float(data: Dict[str, object], *keys: str) -> float:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _quantize_price(price: float, metadata: Dict[str, object]) -> float:
        tick = ParadexVenueAdapter._coerce_float(metadata, "price_tick_size", "tick_size")
        if tick <= 0:
            return price
        steps = round(price / tick)
        return round(steps * tick, 12)

    @staticmethod
    def _quantize_size(size: float, metadata: Dict[str, object]) -> float:
        increment = ParadexVenueAdapter._coerce_float(metadata, "order_size_increment", "size_increment")
        if increment <= 0:
            return size
        steps = round(size / increment)
        return round(steps * increment, 12)

    @staticmethod
    def _ceil_to_increment(size: float, increment: float) -> float:
        if increment <= 0:
            return size
        steps = math.ceil(size / increment)
        return round(steps * increment, 12)
