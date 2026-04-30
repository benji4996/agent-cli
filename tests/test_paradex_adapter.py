from __future__ import annotations

import json

import httpx
import pytest

from adapters.paradex_adapter import ParadexVenueAdapter


class FakeProxy:
    def __init__(self):
        self.submitted_orders = []
        self.fills = []
        self.open_orders = []
        self.cancel_error = None
        self.fetch_orders_error = None

    def get_market_metadata(self, instrument: str):
        return {
            "symbol": instrument,
            "price_tick_size": "0.001",
            "order_size_increment": "0.01",
            "min_notional": "10",
        }

    def get_market_summary(self, instrument: str):
        return {
            "best_bid": "83.895",
            "best_ask": "83.900",
            "mark_price": "83.8975",
            "volume_24h": "3900000",
            "open_interest": "12345",
        }

    def submit_order(self, order):
        self.submitted_orders.append(order)
        return {"id": "order-1"}

    def fetch_fills(self):
        return list(self.fills)

    def fetch_orders(self):
        if self.fetch_orders_error is not None:
            raise self.fetch_orders_error
        return list(self.open_orders)

    def cancel_order(self, oid):
        if self.cancel_error is not None:
            raise self.cancel_error
        return {"id": oid, "status": "CANCELLED"}


def test_get_snapshot_uses_summary_prices():
    adapter = ParadexVenueAdapter(FakeProxy())
    snap = adapter.get_snapshot("SOL-USD-PERP")
    assert snap.instrument == "SOL-USD-PERP"
    assert snap.mid_price == 83.8975
    assert snap.bid == 83.895
    assert snap.ask == 83.9
    assert snap.volume_24h == 3900000.0
    assert snap.open_interest == 12345.0
    assert snap.spread_bps > 0


def test_get_snapshot_treats_read_timeout_as_empty_market_data():
    proxy = FakeProxy()

    def _raise_timeout(instrument: str):
        raise httpx.ReadTimeout("timed out")

    proxy.get_market_metadata = _raise_timeout
    adapter = ParadexVenueAdapter(proxy)
    snap = adapter.get_snapshot("SOL-USD-PERP")
    assert snap.instrument == "SOL-USD-PERP"
    assert snap.mid_price == 0.0
    assert snap.bid == 0.0
    assert snap.ask == 0.0


def test_get_snapshot_treats_bad_gateway_json_decode_as_empty_market_data():
    proxy = FakeProxy()

    def _raise_bad_gateway(instrument: str):
        raise json.JSONDecodeError("Expecting value", "", 0)

    proxy.get_market_metadata = _raise_bad_gateway
    adapter = ParadexVenueAdapter(proxy)
    snap = adapter.get_snapshot("SOL-USD-PERP")
    assert snap.instrument == "SOL-USD-PERP"
    assert snap.mid_price == 0.0
    assert snap.bid == 0.0
    assert snap.ask == 0.0


def test_capabilities_reports_alo_supported():
    adapter = ParadexVenueAdapter(FakeProxy())
    assert adapter.capabilities().supports_alo is True


def test_place_order_does_not_treat_ack_as_fill():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy)
    fill = adapter.place_order("SOL-USD-PERP", "buy", 0.15, 83.9, tif="Gtc")
    assert fill is None
    assert len(proxy.submitted_orders) == 1


def test_cancel_order_treats_missing_exchange_order_as_benign():
    proxy = FakeProxy()
    proxy.cancel_error = ValueError("ApiError(error='ORDER_ID_NOT_FOUND', message='could not find order id', data=None)")
    adapter = ParadexVenueAdapter(proxy)
    assert adapter.cancel_order("SOL-USD-PERP", "missing-order") is True


def test_cancel_order_still_raises_unexpected_errors():
    proxy = FakeProxy()
    proxy.cancel_error = ValueError("ApiError(error='SOMETHING_ELSE', message='unexpected', data=None)")
    adapter = ParadexVenueAdapter(proxy)
    with pytest.raises(ValueError):
        adapter.cancel_order("SOL-USD-PERP", "bad-order")


def test_get_open_orders_treats_read_timeout_as_transient_empty_result():
    proxy = FakeProxy()
    proxy.fetch_orders_error = httpx.ReadTimeout("timed out")
    adapter = ParadexVenueAdapter(proxy)
    assert adapter.get_open_orders("SOL-USD-PERP") == []


def test_place_order_treats_cancel_only_mode_as_noop():
    proxy = FakeProxy()
    proxy.cancel_error = None
    adapter = ParadexVenueAdapter(proxy)
    proxy.submitted_orders.clear()

    def _raise_cancel_only(order):
        raise ValueError("ApiError(error='SYSTEM_STATUS_CANCEL_ONLY', message='system status is CANCEL_ONLY, only cancel orders are allowed', data=None)")

    proxy.submit_order = _raise_cancel_only
    assert adapter.place_order("SOL-USD-PERP", "buy", 0.15, 83.9, tif="Gtc") is None


def test_place_order_strict_mode_skips_sub_min_notional_order():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="strict")
    fill = adapter.place_order("SOL-USD-PERP", "buy", 0.05, 83.9, tif="Ioc")
    assert fill is None
    assert proxy.submitted_orders == []


def test_place_order_auto_bump_mode_raises_size_to_min_notional():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="auto_bump")
    fill = adapter.place_order("SOL-USD-PERP", "buy", 0.05, 83.9, tif="Ioc")
    assert fill is None
    assert len(proxy.submitted_orders) == 1
    assert proxy.submitted_orders[0]["size"] == 0.12


def test_place_order_auto_bump_mode_can_apply_buffer_pct():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="auto_bump", auto_bump_buffer_pct=10.0)
    adapter.place_order("SOL-USD-PERP", "buy", 0.05, 83.9, tif="Ioc")
    assert len(proxy.submitted_orders) == 1
    assert proxy.submitted_orders[0]["size"] == 0.14


def test_place_order_auto_bump_mode_recovers_from_quantized_zero_size():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="auto_bump")
    adapter.place_order("SOL-USD-PERP", "buy", 0.005, 83.9, tif="Ioc")
    assert len(proxy.submitted_orders) == 1
    assert proxy.submitted_orders[0]["size"] == 0.12


def test_place_order_strict_mode_skips_quantized_zero_size():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="strict")
    adapter.place_order("SOL-USD-PERP", "buy", 0.005, 83.9, tif="Ioc")
    assert proxy.submitted_orders == []


def test_place_order_can_forward_reduce_only_for_shutdown_closes():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="auto_bump")
    adapter.place_order("SOL-USD-PERP", "sell", 0.07, 85.671, tif="Ioc", reduce_only=True)
    assert len(proxy.submitted_orders) == 1
    assert proxy.submitted_orders[0]["size"] == 0.12
    assert proxy.submitted_orders[0]["reduce_only"] is True


def test_place_order_can_auto_bump_reduce_only_shutdown_even_when_general_mode_is_strict():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="strict", reduce_only_min_notional_mode="auto_bump")
    adapter.place_order("SOL-USD-PERP", "sell", 0.03, 83.807, tif="Ioc", reduce_only=True)
    assert len(proxy.submitted_orders) == 1
    assert proxy.submitted_orders[0]["size"] == 0.12
    assert proxy.submitted_orders[0]["reduce_only"] is True


def test_collect_new_fills_primes_existing_fills_then_only_returns_new_ones():
    proxy = FakeProxy()
    proxy.fills = [
        {"id": "f1", "market": "SOL-USD-PERP", "side": "BUY", "price": "83.8", "size": "0.15", "created_at": 1000, "fee": "0"},
        {"id": "f2", "market": "SOL-USD-PERP", "side": "SELL", "price": "83.9", "size": "0.15", "created_at": 2000, "fee": "0"},
    ]
    adapter = ParadexVenueAdapter(proxy)
    first = adapter.collect_new_fills("SOL-USD-PERP")
    proxy.fills.append({"id": "f3", "market": "SOL-USD-PERP", "side": "SELL", "price": "84.0", "size": "0.15", "created_at": 3000, "fee": "0"})
    second = adapter.collect_new_fills("SOL-USD-PERP")
    assert first == []
    assert [f.oid for f in second] == ["f3"]


def test_normalize_order_floor_mode_raises_passive_quotes_to_min_notional():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="strict", passive_min_notional_mode="floor")
    normalized = adapter.normalize_order("SOL-USD-PERP", "buy", 0.04, 83.67, tif="Alo")
    assert normalized["size"] == 0.12
    assert normalized["price"] == 83.67


def test_quantize_price_and_size_respect_market_metadata():
    proxy = FakeProxy()
    adapter = ParadexVenueAdapter(proxy)
    assert adapter._quantize_price(86.7144, {"price_tick_size": "0.001"}) == 86.714
    assert adapter._quantize_size(0.154, {"order_size_increment": "0.01"}) == 0.15
