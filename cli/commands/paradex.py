"""Paradex-specific operator commands."""
from __future__ import annotations

import logging
import sys
import time
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Dict

import typer

from adapters.paradex_adapter import ParadexVenueAdapter
from parent.paradex_proxy import ParadexProxy

paradex_app = typer.Typer(help="Paradex operator utilities")
log = logging.getLogger("cli.paradex")
ZERO = Decimal("0")


@paradex_app.command("cleanup-dust")
def cleanup_dust_cmd(
    instrument: str = typer.Argument("SOL-USD-PERP", help="Instrument to inspect and clean up"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
):
    """Operator-invoked cleanup for tiny Paradex residual positions.

    This command is intentionally manual. It inspects the current position and,
    if the residual is below min_notional, executes a two-leg cleanup with an
    explicit confirmation step.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from common.credentials import resolve_wallet_address

    l2_address = resolve_wallet_address("paradex")
    if not l2_address:
        typer.echo("Error: no Paradex L2 address configured", err=True)
        raise typer.Exit(1)

    proxy = ParadexProxy(l2_address=l2_address, testnet=not mainnet)
    proxy.connect()
    adapter = ParadexVenueAdapter(proxy, min_notional_mode="auto_bump")

    state = proxy.get_account_state()
    position = _extract_position(state, instrument)
    if not position:
        typer.echo(f"No {instrument} position found. Nothing to do.")
        raise typer.Exit(0)

    qty = Decimal(str(position.get("size") or "0"))
    side = str(position.get("side") or "").upper()
    if side == "SHORT" and qty > ZERO:
        qty = -qty

    if qty == ZERO:
        typer.echo(f"{instrument} is already flat.")
        raise typer.Exit(0)

    metadata = proxy.get_market_metadata(instrument)
    snap = adapter.get_snapshot(instrument)
    close_side = "sell" if qty > ZERO else "buy"
    reference_price = Decimal(str(snap.bid if close_side == "sell" else snap.ask))
    min_notional = Decimal(str(_coerce_market_float(metadata, "min_notional", "min_trade_value")))
    increment = Decimal(str(_coerce_market_float(metadata, "order_size_increment", "size_increment")))
    abs_qty = abs(qty)
    notional = abs_qty * reference_price

    typer.echo(f"Instrument: {instrument}")
    typer.echo(f"Current residual: {qty}")
    typer.echo(f"Reference price: {reference_price}")
    typer.echo(f"Residual notional: {notional:.6f}")
    typer.echo(f"Min notional: {min_notional:.6f}")

    if min_notional <= ZERO or increment <= ZERO:
        typer.echo("Error: market metadata missing min_notional or size increment", err=True)
        raise typer.Exit(1)

    if notional >= min_notional:
        typer.echo("Residual is already above min_notional. Use a standard manual reduce-only order instead.")
        raise typer.Exit(1)

    min_trade_qty = _ceil_to_increment(min_notional / reference_price, increment)
    overshoot_qty = _ceil_to_increment(min_trade_qty + abs_qty, increment)
    opposite_qty = overshoot_qty - abs_qty
    hedge_side = "buy" if close_side == "sell" else "sell"

    typer.echo("")
    typer.echo("Planned operator cleanup:")
    typer.echo(f"1. Overshoot leg: {close_side.upper()} {overshoot_qty} {instrument} IOC (non-reduce-only)")
    typer.echo(f"2. Hedge leg:    {hedge_side.upper()} about {opposite_qty} {instrument} IOC reduce-only")
    typer.echo("This is intentionally manual because it can temporarily flip the position.")

    if not yes and not typer.confirm("Proceed with Paradex dust cleanup?"):
        raise typer.Exit(0)

    overshoot_price = float(reference_price * (Decimal("0.99") if close_side == "sell" else Decimal("1.01")))
    adapter.place_order(
        instrument=instrument,
        side=close_side,
        size=float(overshoot_qty),
        price=overshoot_price,
        tif="Ioc",
        reduce_only=False,
    )
    time.sleep(1.0)

    state = proxy.get_account_state()
    position = _extract_position(state, instrument)
    qty_after = _position_qty(position)
    typer.echo(f"After overshoot leg: position={qty_after}")

    if qty_after == ZERO:
        typer.echo("Cleanup succeeded in one leg. Position is flat.")
        raise typer.Exit(0)

    hedge_side = "sell" if qty_after > ZERO else "buy"
    snap = adapter.get_snapshot(instrument)
    hedge_price = float((Decimal(str(snap.bid)) if hedge_side == "sell" else Decimal(str(snap.ask))) * (Decimal("0.99") if hedge_side == "sell" else Decimal("1.01")))
    adapter.place_order(
        instrument=instrument,
        side=hedge_side,
        size=float(abs(qty_after)),
        price=hedge_price,
        tif="Ioc",
        reduce_only=True,
    )
    time.sleep(1.0)

    state = proxy.get_account_state()
    final_position = _extract_position(state, instrument)
    final_qty = _position_qty(final_position)
    typer.echo(f"Final position: {final_qty}")
    if final_qty == ZERO:
        typer.echo("Cleanup complete. Position is flat.")
        return

    typer.echo("Cleanup did not flatten completely. Manual review required.", err=True)
    raise typer.Exit(1)



def _extract_position(state: Dict[str, Any], instrument: str) -> Dict[str, Any] | None:
    instrument_upper = instrument.upper()
    for raw in state.get("positions") or []:
        symbol = str(raw.get("market") or raw.get("symbol") or raw.get("instrument") or "")
        if symbol.upper() == instrument_upper:
            return raw
    return None



def _position_qty(position: Dict[str, Any] | None) -> Decimal:
    if not position:
        return ZERO
    qty = Decimal(str(position.get("size") or "0"))
    side = str(position.get("side") or "").upper()
    if side == "SHORT" and qty > ZERO:
        return -qty
    return qty



def _coerce_market_float(data: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0



def _ceil_to_increment(size: Decimal, increment: Decimal) -> Decimal:
    if increment <= ZERO:
        return size
    return (size / increment).to_integral_value(rounding=ROUND_CEILING) * increment
