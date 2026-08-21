"""Learning graph utilities for storing trade decisions and outcomes.

Each trade generates two *quadruplets* (four‑element tuples) that together form two
pairs:

1. **Decision pair** – (trade_id, signal, expected_outcome, timestamp)
2. **Result pair**  – (trade_id, actual_pnl, verdict, timestamp)

The graph is a directed multigraph where the decision node points to the result node.
We store the graph in memory using *networkx* and persist it to a JSON‑Lines file
(`learning_graph.jsonl`) after each backtest run.  The file can later be consumed by
any learning component (RL, LLM, statistical analysis, etc.).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import networkx as nx

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionRecord:
    trade_id: str
    signal: Dict[str, Any]
    expected: Dict[str, Any]
    timestamp: str


@dataclass(frozen=True)
class ResultRecord:
    trade_id: str
    actual_pnl: float
    verdict: str
    timestamp: str
    # Bayesian posterior of PnL distribution (Normal-Normal conjugate)
    posterior_mu: float = 0.0
    posterior_sigma2: float = 1.0
    # RL value estimates (offline bandit / policy gradient)
    rl_value: float = 0.0
    rl_advantage: float = 0.0


class LearningGraph:
    """A thin wrapper around a NetworkX MultiDiGraph for persisting decisions.

    The graph stores two node types – ``decision`` and ``result`` – and a single edge
    linking a decision to its result.  Nodes are identified by a UUID that is also
    used as ``trade_id`` in the backtest ``Trade`` object.
    """

    def __init__(self, storage_path: Path | None = None):
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self.storage_path = storage_path or Path("learning_graph.jsonl")
        # Load existing data if present
        if self.storage_path.exists():
            self._load()

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def add_trade(
        self,
        asset: str = "UNKNOWN",
        entry_time: Optional[datetime] = None,
        signal: Optional[Dict[str, Any]] = None,
        expected: Optional[Dict[str, Any]] = None,
        actual_pnl: float = 0.0,
        verdict: str = "flat",
        # Flexible keyword arguments for post-trade reflection
        trade_id: Optional[str] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        r_multiple: Optional[float] = None,
        net_pnl: Optional[float] = None,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
    ) -> None:
        """Record a completed trade and update Bayesian posterior."""
        if net_pnl is not None:
            actual_pnl = net_pnl
        if symbol is not None:
            asset = symbol
        if trade_id is None:
            now_dt = entry_time or datetime.now(timezone.utc)
            trade_id = f"{asset}-{now_dt.isoformat()}"

        if verdict == "flat" and actual_pnl != 0.0:
            verdict = "win" if actual_pnl > 0 else "loss"

        signal_payload = signal or {
            "strategy": strategy or "momentum",
            "regime": regime or "unknown",
            "entry_price": entry_price or 0.0,
        }
        expected_payload = expected or {
            "r_multiple": r_multiple or 0.0,
            "exit_price": exit_price or 0.0,
        }

        ts = datetime.now(timezone.utc).isoformat()
        decision_node = ("decision", trade_id)
        result_node = ("result", trade_id)

        # Create nodes
        self.graph.add_node(decision_node, data=DecisionRecord(trade_id, signal_payload, expected_payload, ts))
        # Initialize posterior with prior (mu=0, sigma2=1) and observation variance sigma2_obs=0.5
        sigma2_obs = 0.5
        prior_mu = 0.0
        prior_sigma2 = 1.0
        posterior_mu = (prior_mu / prior_sigma2 + actual_pnl / sigma2_obs) / (1 / prior_sigma2 + 1 / sigma2_obs)
        posterior_sigma2 = 1.0 / (1.0 / prior_sigma2 + 1.0 / sigma2_obs)
        self.graph.add_node(
            result_node,
            data=ResultRecord(
                trade_id,
                actual_pnl,
                verdict,
                ts,
                posterior_mu=posterior_mu,
                posterior_sigma2=posterior_sigma2,
                rl_value=0.0,
                rl_advantage=0.0,
            ),
        )
        # Edge stores the relationship; ``key`` is unused but required for MultiDiGraph
        self.graph.add_edge(decision_node, result_node, key=uuid.uuid4().hex)

    def to_jsonl(self) -> None:
        """Append the current graph to the JSON‑Lines storage file.

        Each line now also contains Bayesian posterior fields and RL values.
        """
        lines: list[str] = []
        for decision_node, result_node, data in self.graph.edges(data=True):
            decision: DecisionRecord = self.graph.nodes[decision_node]["data"]
            result: ResultRecord = self.graph.nodes[result_node]["data"]
            line = json.dumps({
                "decision": {
                    "trade_id": decision.trade_id,
                    "signal": decision.signal,
                    "expected": decision.expected,
                    "timestamp": decision.timestamp,
                },
                "result": {
                    "trade_id": result.trade_id,
                    "actual_pnl": result.actual_pnl,
                    "verdict": result.verdict,
                    "timestamp": result.timestamp,
                    "posterior_mu": result.posterior_mu,
                    "posterior_sigma2": result.posterior_sigma2,
                    "rl_value": result.rl_value,
                    "rl_advantage": result.rl_advantage,
                },
            })
            lines.append(line)
        # Write atomically – overwrite the file each time (graph grows monotonically)
        if self.storage_path.parent:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _load(self) -> None:
        """Load an existing JSON‑Lines file into the graph.

        The method recreates nodes and edges exactly as they were saved by
        :meth:`to_jsonl`.  Errors are ignored – a corrupted line is simply skipped.
        """
        with self.storage_path.open("r", encoding="utf-8") as f:
            for raw in f:
                try:
                    payload = json.loads(raw)
                    d = payload["decision"]
                    r = payload["result"]
                    decision_node = ("decision", d["trade_id"])
                    result_node = ("result", r["trade_id"])
                    self.graph.add_node(
                        decision_node,
                        data=DecisionRecord(
                            d["trade_id"], d["signal"], d["expected"], d["timestamp"]
                        ),
                    )
                    self.graph.add_node(
                        result_node,
                        data=ResultRecord(
                            r["trade_id"], r["actual_pnl"], r["verdict"], r["timestamp"],
                            r.get("posterior_mu", 0.0), r.get("posterior_sigma2", 1.0),
                            r.get("rl_value", 0.0), r.get("rl_advantage", 0.0)
                        ),
                    )
                    self.graph.add_edge(decision_node, result_node, key=uuid.uuid4().hex)
                except Exception:
                    # Silently skip malformed lines – they are not critical for learning
                    continue

    # ---------------------------------------------------------------------
    # Convenience utilities & SQLite Bridge
    # ---------------------------------------------------------------------
    def get_trade_records(self) -> list[dict]:
        """Returns a list of dicts with decision + result combined, including posterior and RL values."""
        records = []
        for decision_node, result_node, _ in self.graph.edges(data=True):
            d: DecisionRecord = self.graph.nodes[decision_node]["data"]
            r: ResultRecord = self.graph.nodes[result_node]["data"]
            records.append({
                "trade_id": d.trade_id,
                "asset": d.signal.get("asset"),
                "side": d.signal.get("side"),
                "entry_price": d.signal.get("entry_price"),
                "stop_price": d.expected.get("stop_price"),
                "target_price": d.expected.get("target_price"),
                "actual_pnl": r.actual_pnl,
                "verdict": r.verdict,
                "decision_time": d.timestamp,
                "result_time": r.timestamp,
                "posterior_mu": r.posterior_mu,
                "posterior_sigma2": r.posterior_sigma2,
                "rl_value": r.rl_value,
                "rl_advantage": r.rl_advantage,
            })
        return records

    def export_to_sqlite(self, db_path: Path | str = "learning_graph.db") -> None:
        """Exports graph trade records to a SQLite database for fast indexing/querying.
        Includes Bayesian posterior and RL fields.
        """
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                asset TEXT,
                side TEXT,
                entry_price REAL,
                stop_price REAL,
                target_price REAL,
                actual_pnl REAL,
                verdict TEXT,
                decision_time TEXT,
                result_time TEXT,
                posterior_mu REAL,
                posterior_sigma2 REAL,
                rl_value REAL,
                rl_advantage REAL
            )
        """)
        # Insert all records – upsert on conflict replaces older entry
        for rec in self.get_trade_records():
            # Retrieve posterior and RL values from the underlying ResultRecord
            result_node = ("result", rec["trade_id"])
            result_obj: ResultRecord = self.graph.nodes[result_node]["data"]
            cur.execute(
                "INSERT OR REPLACE INTO trades (trade_id, asset, side, entry_price, stop_price, target_price, actual_pnl, verdict, decision_time, result_time, posterior_mu, posterior_sigma2, rl_value, rl_advantage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec["trade_id"],
                    rec["asset"],
                    rec["side"],
                    rec["entry_price"],
                    rec["stop_price"],
                    rec["target_price"],
                    rec["actual_pnl"],
                    rec["verdict"],
                    rec["decision_time"],
                    rec["result_time"],
                    result_obj.posterior_mu,
                    result_obj.posterior_sigma2,
                    result_obj.rl_value,
                    result_obj.rl_advantage,
                ),
            )
        conn.commit()
        conn.close()

    def get_best_strategies(self, metric: str = "posterior_mu", top_n: int = 5) -> list[dict]:
        """Return the top‑N trade results sorted by the selected metric.

        Parameters
        ----------
        metric: str
            One of "posterior_mu", "rl_value", or "actual_pnl".
        top_n: int
            Number of top records to return.
        """
        if metric not in {"posterior_mu", "rl_value", "actual_pnl"}:
            raise ValueError(f"Unsupported metric: {metric}")
        records = self.get_trade_records()
        sorted_records = sorted(records, key=lambda r: r.get(metric, 0.0), reverse=True)
        return sorted_records[:top_n]

    def dump_summary(self) -> str:
        """Return a short human‑readable summary of the graph.

        Useful for debugging or quick inspection in notebooks.
        """
        total = self.graph.number_of_nodes() // 2  # each trade adds 2 nodes
        hits = sum(1 for _, data in self.graph.nodes(data=True) if isinstance(data["data"], ResultRecord) and data["data"].verdict in ("hit_target", "target"))
        return f"LearningGraph: {total} trades recorded, {hits} hit target."

# ---------------------------------------------------------------------------
# Helper to convert ``Signal`` objects to plain dictionaries (JSON‑serialisable)
# ---------------------------------------------------------------------------

def signal_to_dict(signal: Any) -> Dict[str, Any]:
    """Extract the relevant fields from a ``Signal`` instance.

    The function avoids importing the concrete class to keep the module decoupled
    from the rest of the code base – it works with any object that has the same
    attribute names.
    """
    return {
        "asset": getattr(signal, "asset", None),
        "side": getattr(signal, "side", None).name if getattr(signal, "side", None) else None,
        "entry_price": getattr(signal, "entry_price", None),
        "suggested_stop": getattr(signal, "suggested_stop", None),
        "suggested_target": getattr(signal, "suggested_target", None),
        # Convert datetime to ISO‑8601 string for JSON compatibility
        "timestamp": getattr(signal, "timestamp", None).isoformat()
        if isinstance(getattr(signal, "timestamp", None), datetime)
        else getattr(signal, "timestamp", None),
    }

