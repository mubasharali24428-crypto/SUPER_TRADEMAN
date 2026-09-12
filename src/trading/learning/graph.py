"""Learning graph utilities for storing trade decisions and outcomes.

Each trade generates two records – decision and result – linked by an edge in a
directed multigraph.  The graph is persisted to a JSON-Lines file
(``learning_graph.jsonl``) consumable by any learning component (RL, LLM,
statistical analysis).

Durability contract (SUB-07)
----------------------------
* **Single writer** – writes take an exclusive ``fcntl.flock`` on a
  ``<file>.lock`` sidecar, acquired non-blocking with a bounded retry loop, so
  concurrent writers (heartbeat daemon + backtest) serialise instead of
  clobbering each other's appends.
* **Append + fsync** – :meth:`LearningGraph.to_jsonl` appends in ``'a'`` mode
  and fsyncs; the live file is never truncated by a rewrite.
* **Torn-line quarantine** – malformed lines found on load are appended to a
  ``<file>.corrupt`` sidecar and a WARNING metric log is emitted instead of
  being silently skipped.
* **Compaction** – :meth:`LearningGraph.compact` dedupes edges by
  ``(src, dst, context)`` keeping the *latest* weight/count (file order wins),
  archives the pre-compaction file to ``.jsonl.gz``, and atomically rewrites
  the live file (tmp + fsync + rename).
* **Rotation** – appends that would push the live file past ``rotate_bytes``
  (default 10 MiB) rotate it to a timestamped ``<name>.jsonl.<stamp>.gz``
  archive first.

Load semantics: duplicate edges sharing ``(src, dst, context)`` collapse into
one edge holding the most recently appended record's data.
"""

from __future__ import annotations

import errno
import fcntl
import gzip
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import networkx as nx

try:  # optional metrics hook; absence must never break persistence
    from trading.observability.metrics import MetricsCollector  # type: ignore
except Exception:  # pragma: no cover
    MetricsCollector = None  # type: ignore

logger = logging.getLogger("trading.learning.graph")

# Default size at which the JSONL store rotates into a gzip archive (10 MiB).
DEFAULT_ROTATE_BYTES = 10 * 1024 * 1024


def _metric_incr(name: str, value: float = 1.0) -> None:
    """Best-effort counter increment through the observability layer."""
    if MetricsCollector is None:
        return
    try:
        collector = MetricsCollector.get_instance()  # type: ignore[attr-defined]
    except Exception:
        return
    try:
        if hasattr(collector, "increment"):
            collector.increment(name, value)
        elif hasattr(collector, "inc"):
            collector.inc(name, value)
    except Exception:  # pragma: no cover - metrics never break persistence
        logger.debug("metric increment failed for %s", name, exc_info=True)


class LockAcquireTimeout(RuntimeError):
    """Raised when the single-writer lock cannot be acquired within timeout."""


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

    def __init__(
        self,
        storage_path: Path | None = None,
        *,
        lock_timeout: float = 30.0,
        rotate_bytes: int = DEFAULT_ROTATE_BYTES,
    ):
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self.storage_path = Path(storage_path or "learning_graph.jsonl")
        self.lock_path = self.storage_path.with_name(self.storage_path.name + ".lock")
        self.corrupt_path = self.storage_path.with_name(
            self.storage_path.name + ".corrupt"
        )
        self.lock_timeout = max(0.05, float(lock_timeout))
        self.rotate_bytes = int(rotate_bytes)
        # Torn/malformed lines quarantined during the most recent load.
        self.corrupt_line_count: int = 0
        # Edge keys already flushed by this instance (None == unknown history,
        # e.g. pre-existing file, so first flush writes everything it holds).
        self._flushed_keys: Optional[set] = None
        # Load existing data if present
        if self.storage_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Single-writer lock (flock, LOCK_NB retry loop with timeout)
    # ------------------------------------------------------------------
    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        """Exclusive flock on ``<file>.lock``; retries LOCK_NB until timeout."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + self.lock_timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockAcquireTimeout(
                            f"could not acquire writer lock {self.lock_path} "
                            f"within {self.lock_timeout:.2f}s"
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

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
        self.graph.add_node(
            decision_node,
            data=DecisionRecord(trade_id, signal_payload, expected_payload, ts),
        )
        # Initialize posterior with prior (mu=0, sigma2=1) and observation variance sigma2_obs=0.5
        sigma2_obs = 0.5
        prior_mu = 0.0
        prior_sigma2 = 1.0
        posterior_mu = (prior_mu / prior_sigma2 + actual_pnl / sigma2_obs) / (
            1 / prior_sigma2 + 1 / sigma2_obs
        )
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
        """Append the current graph's *new* edges to the JSONL store.

        Appends (never truncates) under the exclusive single-writer lock and
        fsyncs before release.  Idempotent per instance: edges already written
        by this instance are skipped, so daemon-style repeated flushes do not
        pile up duplicate lines (compaction remains available for cross-writer
        dedupe).  Oversize files rotate to ``.jsonl.gz`` first.
        """
        lines_by_key = self._snapshot_keyed()
        if self._flushed_keys is not None:
            pending = {
                k: ln for k, ln in lines_by_key.items() if k not in self._flushed_keys
            }
        else:
            pending = lines_by_key
        if not pending:
            return
        with self._writer_lock():
            if self._needs_rotation_locked():
                self._rotate_locked()
            with self.storage_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(pending.values()) + "\n")
                f.flush()
                os.fsync(f.fileno())
        self._flushed_keys = set(lines_by_key) | (self._flushed_keys or set())
        _metric_incr("learning_graph_appended_lines_total", len(pending))

    def compact(self) -> Dict[str, int]:
        """Dedupe edges by ``(src, dst, context)`` keeping latest weight/count.

        Compacts the in-memory graph, archives the current on-disk file to
        ``.jsonl.gz``, then atomically rewrites the live file with only the
        surviving (latest-per-key) records.  Returns
        ``{"edges_before", "edges_after", "lines_before", "archived"}``.
        """
        edges_before = self.graph.number_of_edges()
        deduped = self._dedupe_edges(self.graph)
        lines_before = 0
        if self.storage_path.exists():
            with self.storage_path.open("r", encoding="utf-8") as f:
                lines_before = sum(1 for line in f if line.strip())

        lines = [
            self._line_from_edge(u, v, data) for u, v, data in deduped.edges(data=True)
        ]
        archived = False
        with self._writer_lock():
            if self.storage_path.exists():
                self._rotate_locked()
                archived = True
            tmp = self.storage_path.with_name(
                self.storage_path.name + f".tmp.{os.getpid()}.{time.time_ns()}"
            )
            with tmp.open("w", encoding="utf-8") as f:
                if lines:
                    f.write("\n".join(lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.storage_path)
            self._fsync_dir()
        self.graph = deduped
        self._flushed_keys = {self._edge_key(u, v) for u, v in deduped.edges()}
        _metric_incr("learning_graph_compactions_total", 1)
        return {
            "edges_before": edges_before,
            "edges_after": deduped.number_of_edges(),
            "lines_before": lines_before,
            "archived": int(archived),
        }

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _edge_context(self, decision: DecisionRecord, result: ResultRecord) -> str:
        """Stable dedupe context for an edge: strategy|verdict."""
        strat = (
            decision.signal.get("strategy")
            if isinstance(decision.signal, dict)
            else None
        )
        return f"{strat}|{result.verdict}"

    def _record_payload(
        self, decision: DecisionRecord, result: ResultRecord, context: str
    ) -> Dict[str, Any]:
        return {
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
            "context": context,
        }

    def _edge_key(self, u: Any, v: Any) -> Tuple[str, str, str]:
        decision: DecisionRecord = self.graph.nodes[u]["data"]
        result: ResultRecord = self.graph.nodes[v]["data"]
        ctx = self._edge_context(decision, result)
        return (u[0] + ":" + u[1], v[0] + ":" + v[1], ctx)

    def _snapshot_keyed(self) -> Dict[Tuple[str, str, str], str]:
        """Serialise the graph into ``{(src,dst,ctx): json_line}`` (last wins)."""
        keyed: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for u, v, k in self.graph.edges(keys=True):
            decision: DecisionRecord = self.graph.nodes[u]["data"]
            result: ResultRecord = self.graph.nodes[v]["data"]
            keyed[self._edge_key(u, v)] = self._record_payload(
                decision, result, self._edge_key(u, v)[2]
            )
        return {key: json.dumps(payload) for key, payload in keyed.items()}

    def _load(self) -> None:
        """Load the JSONL store, deduping ``(src,dst,context)`` (latest wins).

        Torn/malformed lines are quarantined to ``<file>.corrupt`` with a
        warning metric log instead of being silently skipped.
        """
        corrupt_buf: List[str] = []

        best_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        order: Dict[Tuple[str, str, str], int] = {}
        # Original well-formed lines (kept verbatim if we must purge torn ones).
        good_raw: List[str] = []

        with self.storage_path.open("r", encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                    d = payload["decision"]
                    r = payload["result"]
                    context = str(payload.get("context", ""))
                    key = (
                        f"decision:{d['trade_id']}",
                        f"result:{r['trade_id']}",
                        context,
                    )
                except Exception:
                    corrupt_buf.append(raw if raw.endswith("\n") else raw + "\n")
                    continue
                good_raw.append(raw)
                best_by_key[key] = payload  # later occurrences win
                order.setdefault(key, len(order))

        for key in sorted(order, key=lambda kk: order[kk]):
            payload = best_by_key[key]
            d, r = payload["decision"], payload["result"]
            node_d = DecisionRecord(
                d["trade_id"], d["signal"], d["expected"], d["timestamp"]
            )
            node_r = ResultRecord(
                r["trade_id"],
                r["actual_pnl"],
                r["verdict"],
                r["timestamp"],
                r.get("posterior_mu", 0.0),
                r.get("posterior_sigma2", 1.0),
                r.get("rl_value", 0.0),
                r.get("rl_advantage", 0.0),
            )
            self.graph.add_node(("decision", d["trade_id"]), data=node_d)
            self.graph.add_node(("result", r["trade_id"]), data=node_r)
            edge_key = uuid.uuid5(uuid.NAMESPACE_OID, "|".join(key)).hex
            self.graph.add_edge(
                ("decision", d["trade_id"]),
                ("result", r["trade_id"]),
                key=edge_key,
                weight=1.0,
                count=1,
                ctx=key[2],
            )

        if corrupt_buf:
            self.corrupt_line_count += len(corrupt_buf)
            with self.corrupt_path.open("a", encoding="utf-8") as cf:
                cf.writelines(corrupt_buf)
            logger.warning(
                "quarantined %d torn/malformed learning-graph line(s) to %s",
                len(corrupt_buf),
                self.corrupt_path,
                extra={
                    "event": "learning_graph_quarantine",
                    "metric": "learning_graph_corrupt_lines",
                    "count": len(corrupt_buf),
                    "file": str(self.storage_path),
                },
            )
            _metric_incr("learning_graph_corrupt_lines_total", len(corrupt_buf))

            # Purge the torn lines from the live file so subsequent loads see a
            # clean store (originals remain recoverable in the .corrupt sidecar).
            tmp = self.storage_path.with_name(
                self.storage_path.name + f".tmp.{os.getpid()}.{time.time_ns()}"
            )
            with tmp.open("w", encoding="utf-8") as tf:
                tf.writelines(good_raw)
                tf.flush()
                os.fsync(tf.fileno())
            with self._writer_lock():
                os.replace(tmp, self.storage_path)
                self._fsync_dir()
            logger.info(
                "purged %d quarantined line(s) from %s",
                len(corrupt_buf),
                self.storage_path,
            )

        # Everything just loaded is already on disk; don't re-append on flush.
        self._flushed_keys = {self._edge_key(u, v) for u, v in self.graph.edges()}

    def _dedupe_edges(self, graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
        """Collapse graph edges sharing (src, dst, ctx); latest occurrence wins."""
        keyed: Dict[Tuple[str, str, str], Tuple[Any, Any, Dict[str, Any]]] = {}
        for u, v, k, data in graph.edges(data=True, keys=True):
            key = self._edge_key(u, v)
            keyed[key] = (u, v, dict(data))
        out: nx.MultiDiGraph = nx.MultiDiGraph()
        for _key, (su, sv, data) in keyed.items():
            out.add_node(su, **graph.nodes[su])
            out.add_node(sv, **graph.nodes[sv])
            out.add_edge(su, sv, **data)
        return out

    def _line_from_edge(self, u: Any, v: Any, data: Dict[str, Any]) -> str:
        decision: DecisionRecord = self.graph.nodes[u]["data"]
        result: ResultRecord = self.graph.nodes[v]["data"]
        return json.dumps(
            self._record_payload(decision, result, str(data.get("ctx", "")))
        )

    def _needs_rotation_locked(self) -> bool:
        """Oversize check; caller must hold the writer lock."""
        return (
            self.rotate_bytes > 0
            and self.storage_path.exists()
            and self.storage_path.stat().st_size >= self.rotate_bytes
        )

    def _rotate_locked(self) -> Optional[str]:
        """Rotate the live file to a timestamped gzip archive. Lock held."""
        if not self.storage_path.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive = self.storage_path.with_name(f"{self.storage_path.name}.{stamp}.gz")
        with self.storage_path.open("rb") as src, gzip.open(archive, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        self.storage_path.unlink()
        logger.info("rotated learning graph to %s", archive)
        _metric_incr("learning_graph_rotations_total", 1)
        return str(archive)

    def _fsync_dir(self) -> None:
        try:
            dfd = os.open(str(self.storage_path.parent or "."), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:  # pragma: no cover - some filesystems refuse dir fsync
            pass

    # ---------------------------------------------------------------------
    # Convenience utilities & SQLite Bridge
    # ---------------------------------------------------------------------
    def get_trade_records(self) -> list[dict]:
        """Returns a list of dicts with decision + result combined, including posterior and RL values."""
        records = []
        for decision_node, result_node, _ in self.graph.edges(data=True):
            d: DecisionRecord = self.graph.nodes[decision_node]["data"]
            r: ResultRecord = self.graph.nodes[result_node]["data"]
            records.append(
                {
                    "trade_id": d.trade_id,
                    "asset": d.signal.get("asset"),
                    "side": d.signal.get("side"),
                    # R2 / VA-022: strategy label lives in the decision signal
                    # payload (see add_trade) -- surfaced explicitly so batch
                    # trainers attribute outcomes by NAME, not by position.
                    "strategy": d.signal.get("strategy"),
                    "regime": d.signal.get("regime"),
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
                }
            )
        return records

    def export_to_sqlite(self, db_path: Path | str = "learning_graph.db") -> None:
        """Exports graph trade records to a SQLite database for fast indexing/querying.
        Includes Bayesian posterior and RL fields.
        """
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            """
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
        """
        )
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

    def get_best_strategies(
        self, metric: str = "posterior_mu", top_n: int = 5
    ) -> list[dict]:
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
        hits = sum(
            1
            for _, data in self.graph.nodes(data=True)
            if isinstance(data["data"], ResultRecord)
            and data["data"].verdict in ("hit_target", "target")
        )
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
        "side": getattr(signal, "side", None).name
        if getattr(signal, "side", None)
        else None,
        # R2 / VA-022: strategy/regime belong in every decision signal payload
        # so recorded trades can be attributed by strategy NAME downstream
        # (mirrors add_trade's default payload schema).
        "strategy": getattr(signal, "strategy", None),
        "regime": getattr(signal, "regime", None),
        "entry_price": getattr(signal, "entry_price", None),
        "suggested_stop": getattr(signal, "suggested_stop", None),
        "suggested_target": getattr(signal, "suggested_target", None),
        # Convert datetime to ISO‑8601 string for JSON compatibility
        "timestamp": getattr(signal, "timestamp", None).isoformat()
        if isinstance(getattr(signal, "timestamp", None), datetime)
        else getattr(signal, "timestamp", None),
    }
