# Contributing — SUPER_TRADEMAN

## 1. Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo-url> && cd algo-trading-system
uv sync --locked                 # installs exact locked deps into .venv
source .venv/bin/activate        # or prefix everything with `uv run`

# Sanity check
.venv/bin/python -m pytest tests/test_indicators.py -q
```

Never commit real credentials. Copy `.env.example` for placeholder reference
only — real secrets live in an external secret store and are loaded into the
environment (compose manifests fail closed if required vars are unset).

### Tooling

```bash
pre-commit install               # hooks: black, isort, flake8, bandit(HIGH), semgrep(ERROR), mypy
pre-commit run --all-files       # run the full gate locally before pushing
```

Bandit is configured to fail only on HIGH severity findings; semgrep fails on
ERROR-severity rules (`p/ci`, `p/secrets`). LOW/MEDIUM advisory noise stays out
of the blocking path on purpose.

## 2. Branching & Pull Requests

- Branch from latest `main`: `git checkout -b <area>/<short-desc>`
  (e.g. `risk/garch-calibration`, `ops/deploy-hardening`).
- Deployments run ONLY from `main` (`deploy.sh` enforces this).
- One logical change per PR; keep diffs reviewable.
- PR description must state: what changed, why, how it was verified.
- CI (or local equivalent) must pass: pytest suite + pre-commit hooks.
- Never commit directly to `main`; no force-pushes to shared branches.

## 3. Definition of Done — checklist

A PR is done when ALL of the following hold:

- [ ] **Tests** — new/changed behavior has tests; full suite green locally:
      `.venv/bin/python -m pytest -q`
- [ ] **Coverage delta** — coverage of `src/` does not decrease
      (`[tool.coverage]` enforces `fail_under = 60`; report the delta in the PR).
- [ ] **Findings-ID linkage** — if the change traces to an audit finding
      (e.g. `F-0059`, `G-026`), the PR body AND relevant code comment/docstring
      reference that ID so audits can trace remediation.
- [ ] **Doc update** — user/operator-facing changes update the matching doc
      (`README.md`, `docs/DEPLOYMENT_RUNBOOK.md`, `docs/INCIDENT_RESPONSE.md`,
      module docstrings). Unverifiable claims get explicit `TODO(verify)` marks.
- [ ] Pre-commit passes on all touched files.
- [ ] No secrets in code, tests, fixtures, or logs.

## 4. Test conventions

- Shared fixtures live in `tests/conftest.py`: seeded `rng`
  (`TEST_SEED` env, default 8675309), `account_state` factory,
  `tmp_learning_graph`, `candle_frame` / `flat_candles` factories.
  Do NOT hand-roll fresh copies of these.
- Markers: `@pytest.mark.fast` / `slow` / `integration` (enforced via
  `--strict-markers`). Keep the default tier fast; heavy fitting goes under
  `slow`, anything needing services goes under `integration`.
- Determinism: any randomness must come from the seeded `rng` fixture so a
  failure replays with `TEST_SEED=<seed>`.
