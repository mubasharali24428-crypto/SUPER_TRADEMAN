import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from trading.risk.models import AccountState, ApprovedOrder, RiskConfig, Signal, Side
from trading.risk.engine import RiskEngine
from trading.risk.garch import GARCHVolatilityModel, GARCHForecastResult
from trading.risk.hmm_regime import HMMRegimeClassifier, HMMRegimeResult
from trading.risk.evt import EVTRiskEngine, EVTRiskResult
from trading.risk.copula import CopulaDependencyEngine, CopulaDependencyResult
from trading.risk.survival import SurvivalEngine, SurvivalTier, AccountSurvivalStatus
from trading.learning.graph import LearningGraph
from trading.learning.policy import ContextualBanditAllocator

logger = logging.getLogger("trading.daemon.heartbeat")


@dataclass
class HeartbeatCycleResult:
    """Telemetry report produced by a single heartbeat tick."""
    cycle_number: int
    timestamp: datetime
    survival_status: AccountSurvivalStatus
    garch_forecast: GARCHForecastResult
    hmm_regime: HMMRegimeResult
    evt_tail_risk: EVTRiskResult
    active_strategy: str
    proposed_signals: int
    approved_orders: List[ApprovedOrder]
    elapsed_ms: float


class TradingHeartbeatDaemon:
    """Autonomous Heartbeat Daemon (Automaton-Inspired Continuous Trading Loop).

    Orchestrates the entire statistical, survival, strategy allocation,
    and sovereign risk gating pipeline in an asynchronous, non-blocking loop.
    """

    def __init__(
        self,
        symbols: Sequence[str] = ("BTC/USDT", "ETH/USDT", "SOL/USDT"),
        risk_config: Optional[RiskConfig] = None,
        learning_graph: Optional[LearningGraph] = None,
        bandit_allocator: Optional[ContextualBanditAllocator] = None,
        interval_seconds: float = 60.0,
    ):
        self.symbols = list(symbols)
        self.risk_config = risk_config or RiskConfig()
        self.risk_engine = RiskEngine(self.risk_config)
        self.survival_engine = SurvivalEngine(self.risk_config)
        self.garch_model = GARCHVolatilityModel()
        self.hmm_classifier = HMMRegimeClassifier()
        self.evt_engine = EVTRiskEngine()
        self.copula_engine = CopulaDependencyEngine()

        self.learning_graph = learning_graph or LearningGraph()
        self.bandit_allocator = bandit_allocator or ContextualBanditAllocator(
            strategies=["trend_momentum", "mean_reversion", "breakout"]
        )

        self.interval_seconds = interval_seconds
        self.is_running = False
        self.cycle_count = 0
        self.latest_history: Dict[str, List[float]] = {s: [] for s in self.symbols}

    def feed_market_data(self, symbol: str, prices: Sequence[float]):
        """Updates internal price history buffer for a given symbol."""
        if symbol in self.latest_history:
            self.latest_history[symbol].extend(prices)
            # Keep rolling window of last 500 prices
            if len(self.latest_history[symbol]) > 500:
                self.latest_history[symbol] = self.latest_history[symbol][-500:]

    def tick_cycle(
        self,
        account: AccountState,
        signal_generator_fn: Optional[Callable[[str, str, float], Optional[Signal]]] = None,
    ) -> HeartbeatCycleResult:
        """Executes a single synchronous 'Think -> Act -> Observe -> Reflect' cycle."""
        t0 = time.perf_counter()
        self.cycle_count += 1
        now = datetime.now(timezone.utc)

        primary_symbol = self.symbols[0] if self.symbols else "BTC/USDT"
        prices = self.latest_history.get(primary_symbol, [])
        if len(prices) < 30:
            # Fallback synthetic seed if buffer is short
            prices = [100.0 * (1.0 + 0.001 * i) for i in range(40)]

        # 1. Statistical Modeling (GARCH, HMM, EVT)
        garch_res = self.garch_model.fit_forecast(prices)
        hmm_res = self.hmm_classifier.fit_predict(prices)
        evt_res = self.evt_engine.estimate_tail_risk(prices)

        # 2. Survival Evaluation
        survival_status = self.survival_engine.evaluate_survival_status(
            account=account,
            garch_res=garch_res,
            hmm_res=hmm_res,
            evt_res=evt_res,
        )

        # 3. Strategy Selection (Think)
        # Feed HMM regime probabilities directly into the Contextual Bandit
        active_strategy = self.bandit_allocator.select_strategy(context=hmm_res.state_probabilities)

        approved_orders: List[ApprovedOrder] = []
        proposed_signals_count = 0

        # 4. Signal Generation & Risk Gating (Act)
        if survival_status.allow_new_entries and signal_generator_fn is not None:
            for symbol in self.symbols:
                sym_prices = self.latest_history.get(symbol, prices)
                current_price = sym_prices[-1] if sym_prices else 100.0
                signal = signal_generator_fn(symbol, active_strategy, current_price)
                if signal is not None:
                    proposed_signals_count += 1
                    # Check survival confidence floor
                    if signal.confidence >= survival_status.min_confidence_floor:
                        # Apply GARCH & Survival risk scaling factor
                        garch_scale = garch_res.volatility_scale_factor * survival_status.effective_risk_multiplier
                        # Reconstruct signal with garch scaling
                        scaled_signal = Signal(
                            asset=signal.asset,
                            asset_class=signal.asset_class,
                            side=signal.side,
                            entry_price=signal.entry_price,
                            confidence=signal.confidence,
                            timestamp=signal.timestamp,
                            rationale=signal.rationale,
                            suggested_stop=signal.suggested_stop,
                            suggested_target=signal.suggested_target,
                            garch_vol_scale=garch_scale,
                        )
                        decision = self.risk_engine.evaluate(scaled_signal, account)
                        if decision.approved and decision.approved_order is not None:
                            approved_orders.append(decision.approved_order)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        return HeartbeatCycleResult(
            cycle_number=self.cycle_count,
            timestamp=now,
            survival_status=survival_status,
            garch_forecast=garch_res,
            hmm_regime=hmm_res,
            evt_tail_risk=evt_res,
            active_strategy=active_strategy,
            proposed_signals=proposed_signals_count,
            approved_orders=approved_orders,
            elapsed_ms=elapsed_ms,
        )

    def record_closed_trade_reflection(
        self,
        trade_id: str,
        symbol: str,
        strategy: str,
        regime: str,
        r_multiple: float,
        net_pnl: float,
        entry_price: float,
        exit_price: float,
    ):
        """Autonomous Post-Trade Reflection & Memory Update.

        Records decision-outcome pair in LearningGraph and updates Bandit & Bayesian weights.
        """
        # 1. Update LearningGraph with Bayesian posterior
        self.learning_graph.add_trade(
            trade_id=trade_id,
            symbol=symbol,
            strategy=strategy,
            regime=regime,
            r_multiple=r_multiple,
            net_pnl=net_pnl,
            entry_price=entry_price,
            exit_price=exit_price,
        )

        # 2. Update Contextual Bandit Policy Gradient
        self.bandit_allocator.update_from_trade(strategy=strategy, reward_r=r_multiple)

    async def run_async_loop(
        self,
        account_provider: Callable[[], AccountState],
        signal_generator_fn: Optional[Callable[[str, str, float], Optional[Signal]]] = None,
        max_cycles: Optional[int] = None,
    ):
        """Starts the infinite or bounded asynchronous heartbeat loop."""
        self.is_running = True
        logger.info("TradingHeartbeatDaemon started. Interval: %s sec", self.interval_seconds)

        while self.is_running:
            account = account_provider()
            res = self.tick_cycle(account, signal_generator_fn)
            logger.info(
                "Heartbeat Cycle #%d | Tier=%s | Regime=%s | Strat=%s | Orders=%d | %0.2fms",
                res.cycle_number,
                res.survival_status.tier.value,
                res.hmm_regime.current_regime,
                res.active_strategy,
                len(res.approved_orders),
                res.elapsed_ms,
            )

            if max_cycles is not None and self.cycle_count >= max_cycles:
                self.is_running = False
                break

            await asyncio.sleep(self.interval_seconds)
