"""Tests for the Risk Guardian 3-state gate machine (Phase 3e)."""
from __future__ import annotations

import pytest

from parent.position_tracker import PositionTracker
from parent.risk_manager import RiskGate, RiskManager, RiskState


@pytest.fixture
def rm() -> RiskManager:
    """Fresh RiskManager with gate configured for fast testing."""
    mgr = RiskManager()
    mgr.configure_gate(
        cooldown_duration_ms=1_800_000,
        cooldown_trigger_losses=2,
        cooldown_drawdown_pct=50.0,
        cooldown_close_losses=2,
    )
    return mgr


# ── Default state ────────────────────────────────────────────────

def test_default_state_is_open(rm: RiskManager):
    assert rm.state.risk_gate == RiskGate.OPEN
    assert rm.state.consecutive_losses == 0


# ── Consecutive losses ───────────────────────────────────────────

def test_single_loss_no_cooldown(rm: RiskManager):
    rm.record_loss(now_ms=1000)
    assert rm.state.risk_gate == RiskGate.OPEN
    assert rm.state.consecutive_losses == 1


def test_two_losses_triggers_cooldown(rm: RiskManager):
    rm.record_loss(now_ms=1000)
    rm.record_loss(now_ms=2000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    assert rm.state.cooldown_entered_ts == 2000


def test_win_resets_consecutive_losses(rm: RiskManager):
    rm.record_loss(now_ms=1000)
    assert rm.state.consecutive_losses == 1
    rm.record_win()
    assert rm.state.consecutive_losses == 0
    # A single loss after a win should not trigger cooldown
    rm.record_loss(now_ms=3000)
    assert rm.state.risk_gate == RiskGate.OPEN


# ── Cooldown auto-expiry ────────────────────────────────────────

def test_cooldown_auto_expires_after_duration(rm: RiskManager):
    rm.record_loss(now_ms=0)
    rm.record_loss(now_ms=100)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    # 30 min later (1_800_000 ms)
    rm.check_auto_expiry(now_ms=100 + 1_800_000)
    assert rm.state.risk_gate == RiskGate.OPEN
    assert rm.state.consecutive_losses == 0


def test_cooldown_does_not_expire_early(rm: RiskManager):
    rm.record_loss(now_ms=0)
    rm.record_loss(now_ms=100)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    # Only 10 min later
    rm.check_auto_expiry(now_ms=100 + 600_000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN


# ── Drawdown triggers ───────────────────────────────────────────

def test_drawdown_triggers_cooldown(rm: RiskManager):
    # 50% of limit = 250 out of 500
    rm.check_drawdown(current_drawdown=260.0, limit=500.0)
    assert rm.state.risk_gate == RiskGate.COOLDOWN


def test_drawdown_below_threshold_no_change(rm: RiskManager):
    rm.check_drawdown(current_drawdown=200.0, limit=500.0)
    assert rm.state.risk_gate == RiskGate.OPEN


# ── Daily loss triggers CLOSED ───────────────────────────────────

def test_daily_loss_triggers_closed(rm: RiskManager):
    rm.check_daily_loss(daily_loss=500.0, limit=500.0)
    assert rm.state.risk_gate == RiskGate.CLOSED
    assert rm.state.safe_mode is True


# ── Escalation: COOLDOWN losses extend cooldown before CLOSED ───

def test_cooldown_loss_extends_without_immediate_close(rm: RiskManager):
    rm.record_loss(now_ms=1000)
    rm.record_loss(now_ms=2000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    # First additional loss during cooldown should extend cooldown, not close.
    rm.record_loss(now_ms=3000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    assert rm.state.cooldown_losses == 1
    assert rm.state.cooldown_entered_ts == 3000


def test_cooldown_repeated_loss_escalates_to_closed(rm: RiskManager):
    rm.record_loss(now_ms=1000)
    rm.record_loss(now_ms=2000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    rm.record_loss(now_ms=3000)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    rm.record_loss(now_ms=4000)
    assert rm.state.risk_gate == RiskGate.CLOSED


# ── Daily reset ──────────────────────────────────────────────────

def test_daily_reset_closed_to_open(rm: RiskManager):
    rm.check_daily_loss(daily_loss=600.0, limit=500.0)
    assert rm.state.risk_gate == RiskGate.CLOSED
    rm.daily_reset()
    assert rm.state.risk_gate == RiskGate.OPEN
    assert rm.state.consecutive_losses == 0
    assert rm.state.cooldown_entered_ts == 0


def test_automatic_day_boundary_reset_keeps_daily_drawdown_safe_mode_active(rm: RiskManager):
    rm.state.safe_mode = True
    rm.state.safe_mode_reason = "daily_drawdown_breach"
    rm.state.reduce_only = True
    rm.state.daily_drawdown = rm.limits.max_daily_drawdown_abs
    rm.state.day_start_ms = 0

    ok, reason = rm.pre_round_check(positions=PositionTracker(), mark_prices={})

    assert ok is False
    assert reason == "Safe mode active: daily_drawdown_breach"
    assert rm.state.safe_mode is True
    assert rm.state.safe_mode_reason == "daily_drawdown_breach"
    assert rm.state.reduce_only is True


# ── can_open_position / can_trade ────────────────────────────────

def test_can_open_position_open(rm: RiskManager):
    assert rm.can_open_position() is True


def test_can_open_position_cooldown(rm: RiskManager):
    rm.record_loss(now_ms=0)
    rm.record_loss(now_ms=1)
    assert rm.state.risk_gate == RiskGate.COOLDOWN
    assert rm.can_open_position() is False


def test_can_open_position_closed(rm: RiskManager):
    rm.check_daily_loss(daily_loss=999.0, limit=500.0)
    assert rm.can_open_position() is False


def test_can_trade_open(rm: RiskManager):
    assert rm.can_trade() is True


def test_can_trade_cooldown(rm: RiskManager):
    rm.record_loss(now_ms=0)
    rm.record_loss(now_ms=1)
    assert rm.can_trade() is True


def test_can_trade_closed(rm: RiskManager):
    rm.check_daily_loss(daily_loss=999.0, limit=500.0)
    assert rm.can_trade() is False


# ── Serialization round-trip ─────────────────────────────────────

def test_risk_state_serialization_roundtrip():
    state = RiskState(
        risk_gate=RiskGate.COOLDOWN,
        consecutive_losses=3,
        cooldown_entered_ts=12345,
    )
    d = state.to_dict()
    assert d["risk_gate"] == "COOLDOWN"
    restored = RiskState.from_dict(d)
    assert restored.risk_gate == RiskGate.COOLDOWN
    assert restored.consecutive_losses == 3
    assert restored.cooldown_entered_ts == 12345
