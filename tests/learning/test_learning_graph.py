"""Learning-graph durability tests (SUB-07).

Covers: two-writer flock concurrency (zero lost records), compaction dedupe,
rotation to gzip archives, and torn-line quarantine sidecar.
"""

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from trading.learning.graph import (DEFAULT_ROTATE_BYTES, LearningGraph,
                                    LockAcquireTimeout)


@pytest.fixture()
def store(tmp_path) -> Path:
    return tmp_path / "learning_graph.jsonl"


def _writer_main(path: str, writer_id: int, n_trades: int, lock_timeout: float = 60.0):
    """Child-process entry: append n_trades under the flock protocol."""
    lg = LearningGraph(storage_path=Path(path), lock_timeout=lock_timeout)
    for i in range(n_trades):
        lg.add_trade(
            trade_id=f"w{writer_id}-{i}",
            strategy="strat",
            verdict="win",
            net_pnl=1.0,
        )
        lg.to_jsonl()


def test_two_writers_no_lost_records(store):
    """Two concurrent processes appending interleaved must lose zero records."""
    n_writers = 2
    n_each = 30
    procs = [
        mp.Process(target=_writer_main, args=(str(store), wid, n_each))
        for wid in range(n_writers)
    ]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(timeout=120)
        assert pr.exitcode == 0

    expected = n_writers * n_each
    lines = [ln for ln in store.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == expected  # zero lost records

    # Every line parses; every trade id present exactly once.
    ids = []
    for ln in lines:
        payload = json.loads(ln)
        ids.append(payload["decision"]["trade_id"])
    assert len(set(ids)) == expected
    for wid in range(n_writers):
        for i in range(n_each):
            assert f"w{wid}-{i}" in ids


def test_compaction_dedupes_edges_keeps_latest(store):
    g = LearningGraph(storage_path=store)
    g.add_trade(trade_id="t1", strategy="mom", verdict="win", net_pnl=5.0)
    g.to_jsonl()

    g2 = LearningGraph(storage_path=store)
    # Same trade re-recorded with updated pnl => same (src, dst, context).
    g2.add_trade(trade_id="t1", strategy="mom", verdict="win", net_pnl=9.0)
    stats = g2.compact()
    assert stats["edges_before"] == 2
    assert stats["edges_after"] == 1
    assert stats["archived"] == 1

    # Latest record wins.
    recs = g2.get_trade_records()
    assert len(recs) == 1
    assert recs[0]["actual_pnl"] == 9.0
    assert recs[0]["posterior_mu"] > 0.5  # posterior moved with pnl=9.0

    # Live file now holds exactly one record; pre-compaction data archived.
    lines = [ln for ln in store.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    archives = list(store.parent.glob("learning_graph.jsonl.*.gz"))
    assert len(archives) >= 1


def test_rotation_at_threshold(store):
    g = LearningGraph(storage_path=store, rotate_bytes=300)
    for i in range(6):
        g.add_trade(trade_id=f"r{i}", strategy="s", verdict="win", net_pnl=1.0)
        g.to_jsonl()
    archives = sorted(store.parent.glob("learning_graph.jsonl.*.gz"))
    assert len(archives) >= 2  # rotated at least twice at 300B threshold
    # Archive content is valid gzip JSONL containing earlier trades.
    import gzip

    with gzip.open(archives[0], "rt", encoding="utf-8") as f:
        archived_lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert json.loads(archived_lines[0])["decision"]["trade_id"] == "r0"
    live_ids = [
        json.loads(ln)["decision"]["trade_id"]
        for ln in store.read_text().splitlines()
        if ln.strip()
    ]
    assert "r5" in live_ids


def test_torn_line_quarantine_sidecar(store):
    g = LearningGraph(storage_path=store)
    g.add_trade(trade_id="ok-1", strategy="s", verdict="win", net_pnl=2.0)
    g.to_jsonl()
    good_lines = len(store.read_text().splitlines())
    with store.open("a", encoding="utf-8") as f:
        f.write('{"decision": torn\n')  # truncated mid-object
        f.write("not json at all\n")  # garbage

    corrupt_path = Path(str(store) + ".corrupt")
    assert not corrupt_path.exists() or corrupt_path.stat().st_size == 0

    g2 = LearningGraph(storage_path=store)
    assert g2.corrupt_line_count == 2
    quarantined = corrupt_path.read_text().splitlines()
    assert len(quarantined) == 2
    assert '{"decision": torn' in quarantined[0]

    # Good records survived and are not re-appended on flush.
    assert g2.graph.number_of_edges() == 1
    g2.to_jsonl()
    assert (
        len([ln for ln in store.read_text().splitlines() if ln.strip()]) == good_lines
    )


def test_lock_timeout_raises(store, tmp_path):
    """A held lock forces acquisition failure after the timeout budget."""
    holder_path = tmp_path / "held.lock"
    fd = holder_path.open("w")
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tiny = tmp_path / "tiny.jsonl"
        lg_holder = LearningGraph(storage_path=tiny, lock_timeout=0.2)
        lg_holder.lock_path = holder_path  # point at the held lock
        with pytest.raises(LockAcquireTimeout):
            with lg_holder._writer_lock():
                pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_lock_contention_inprocess_serialises(store):
    """Same-process contention serialises instead of clobbering."""
    g1 = LearningGraph(storage_path=store, lock_timeout=10.0)
    g2 = LearningGraph(storage_path=store, lock_timeout=10.0)
    g1.add_trade(trade_id="a", strategy="s", verdict="win", net_pnl=1.0)
    g2.add_trade(trade_id="b", strategy="s", verdict="loss", net_pnl=-1.0)
    g1.to_jsonl()
    g2.to_jsonl()
    lines = [ln for ln in store.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
