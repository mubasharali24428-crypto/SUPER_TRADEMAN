from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Private construction token. ApprovedOrder checks identity against this object,
# so only code that imports it (RiskEngine) can mint a valid order.
_ISSUER = object()


class Side(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Signal:
    asset: str
    asset_class: str
    side: Side
    entry_price: float
    confidence: float
    timestamp: datetime
    rationale: str
    suggested_stop: float | None = None
    suggested_target: float | None = None


@dataclass(frozen=True)
class Position:
    asset: str
    asset_class: str
    side: Side
    entry_price: float
    stop_price: float
    risk_pct: float  # fraction of account equity this position risks


@dataclass(frozen=True)
class RiskConfig:
    risk_pct: float = 0.01  # 1-2% per trade, hard capped below
    min_reward_risk: float = 2.0
    max_heat: float = 0.06
    max_heat_high_vol: float = 0.04
    correlation_threshold: float = 0.7
    max_positions_per_asset_class: int = 5
    daily_loss_limit: float = 0.025  # 2-3%
    weekly_loss_limit: float = 0.055  # 5-6%
    weekly_loss_reduction: float = 0.5
    max_drawdown: float = 0.175  # 15-20%
    consecutive_loss_limit: int = 4  # 3-5
    cooldown_hours: float = 4
    pre_event_hours: float = 2
    pre_event_reduction: float = 0.5
    min_exit_confidence: float = 0.5  # floor for third-party (e.g. LLM) exit/reduce-risk signals

    def __post_init__(self):
        if not 0 < self.risk_pct <= 0.02:
            raise ValueError("risk_pct must be within (0, 0.02] per hard cap")


@dataclass
class AccountState:
    equity: float
    peak_equity: float
    open_positions: list[Position] = field(default_factory=list)
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    consecutive_losses: dict[str, int] = field(default_factory=dict)
    last_loss_at: dict[str, datetime] = field(default_factory=dict)
    kill_switch: bool = False
    high_volatility: bool = False
    minutes_to_next_major_event: float | None = None
    correlations: dict[frozenset, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovedOrder:
    asset: str
    asset_class: str
    side: Side
    entry_price: float
    stop_price: float
    target_price: float
    position_size: float
    risk_pct: float
    issuer: object = field(repr=False)

    def __post_init__(self):
        if self.issuer is not _ISSUER:
            raise PermissionError("ApprovedOrder can only be constructed by the Risk Engine")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    signal: Signal
    approved_order: ApprovedOrder | None = None


@dataclass(frozen=True)
class ExitSignal:
    """A proposal to close an existing open position early -- e.g. from an LLM
    anomaly/sentiment component. This is the only channel by which such a
    component may influence a live position: it can propose closing risk,
    never opening it, never resizing it, and it still must clear the Risk
    Engine's deterministic gate below."""

    asset: str
    asset_class: str
    reason: str
    source: str  # "llm" | "circuit_breaker" | "manual" | ...
    confidence: float
    timestamp: datetime


@dataclass(frozen=True)
class ApprovedExit:
    asset: str
    asset_class: str
    reason: str
    issuer: object = field(repr=False)

    def __post_init__(self):
        if self.issuer is not _ISSUER:
            raise PermissionError("ApprovedExit can only be constructed by the Risk Engine")


@dataclass(frozen=True)
class ExitDecision:
    approved: bool
    reason: str
    signal: ExitSignal
    approved_exit: ApprovedExit | None = None
