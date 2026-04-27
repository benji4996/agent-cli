"""hl arb — cross-venue arbitrage commands."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from click.core import ParameterSource

from cli.config import TradingConfig
from cli.cross_venue_engine import CrossVenueEngine
from cli.venue_factory import build_venue_adapter
from strategies.cross_exchange_arb import CrossExchangeArbStrategy

arb_app = typer.Typer(help="Cross-venue arbitrage utilities")


def _load_yaml(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _section(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key) or {}
    return value if isinstance(value, dict) else {}


def _explicit(ctx: typer.Context, name: str) -> bool:
    return ctx.get_parameter_source(name) in {
        ParameterSource.COMMANDLINE,
        ParameterSource.ENVIRONMENT,
        ParameterSource.PROMPT,
    }


@arb_app.command("run")
def arb_run_cmd(
    ctx: typer.Context,
    asset: str = typer.Option("SOL", "--asset", help="Base asset to arbitrage"),
    hl_instrument: str = typer.Option("SOL-PERP", "--hl-instrument", help="Hyperliquid instrument"),
    paradex_instrument: str = typer.Option("SOL-USD-PERP", "--paradex-instrument", help="Paradex instrument"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Cross-venue arb YAML config"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet for both venues"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Log opportunities without placing orders by default"),
    mock: bool = typer.Option(False, "--mock", help="Use mock adapters for local testing"),
    tick_interval: float = typer.Option(2.0, "--tick", "-t", help="Seconds between ticks"),
    max_ticks: int = typer.Option(0, "--max-ticks", help="Stop after N ticks; 0 runs forever"),
    data_dir: str = typer.Option("data/cross-venue-arb", "--data-dir", help="State/log directory"),
):
    """Run a minimal HL ↔ Paradex cross-venue arbitrage loop."""
    cfg_data = _load_yaml(config)
    strategy_params = _section(cfg_data, "strategy_params")
    risk = _section(cfg_data, "risk")
    execution = _section(cfg_data, "execution")
    venues = _section(cfg_data, "venues")
    hl_cfg_data = _section(venues, "hl")
    pdx_cfg_data = _section(venues, "paradex")

    if config and not _explicit(ctx, "asset"):
        asset = str(cfg_data.get("asset", asset))
    if config and not _explicit(ctx, "hl_instrument"):
        hl_instrument = str(hl_cfg_data.get("instrument", cfg_data.get("hl_instrument", hl_instrument)))
    if config and not _explicit(ctx, "paradex_instrument"):
        paradex_instrument = str(pdx_cfg_data.get("instrument", cfg_data.get("paradex_instrument", paradex_instrument)))
    if config and not _explicit(ctx, "mainnet"):
        mainnet = bool(cfg_data.get("mainnet", mainnet))
    if config and not _explicit(ctx, "tick_interval"):
        tick_interval = float(cfg_data.get("tick_interval", tick_interval))
    if config and not _explicit(ctx, "max_ticks"):
        max_ticks = int(cfg_data.get("max_ticks", max_ticks))
    if config and not _explicit(ctx, "data_dir"):
        data_dir = str(cfg_data.get("data_dir", data_dir))

    logging.basicConfig(
        level=getattr(logging, str(cfg_data.get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(name)-24s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    hl_cfg = TradingConfig(venue="hl", instrument=hl_instrument, mainnet=mainnet, execution=execution)
    pdx_cfg = TradingConfig(venue="paradex", instrument=paradex_instrument, mainnet=mainnet, execution=execution)

    typer.echo(f"Arb asset: {asset}")
    typer.echo(f"HL instrument: {hl_instrument}")
    typer.echo(f"Paradex instrument: {paradex_instrument}")
    typer.echo(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    try:
        hl_adapter, _ = build_venue_adapter(venue="hl", mainnet=mainnet, mock=mock, cfg=hl_cfg)
        paradex_adapter, _ = build_venue_adapter(venue="paradex", mainnet=mainnet, mock=mock, cfg=pdx_cfg)
    except Exception as e:
        typer.echo(f"Error building venue adapters: {e}", err=True)
        raise typer.Exit(1)

    strategy = CrossExchangeArbStrategy(**strategy_params)
    engine = CrossVenueEngine(
        hl_adapter=hl_adapter,
        paradex_adapter=paradex_adapter,
        strategy=strategy,
        asset=asset,
        hl_instrument=hl_instrument,
        paradex_instrument=paradex_instrument,
        tick_interval=tick_interval,
        dry_run=dry_run,
        data_dir=data_dir,
        max_residual_qty=float(risk.get("max_residual_qty", 0.03)),
        hedge_repair_attempts=int(execution.get("hedge_repair_attempts", 0)),
    )
    engine.run(max_ticks=max_ticks)
