# Security Policy

## Supported Versions

Security fixes are applied to the `main` branch. No tagged releases exist yet;
once they do, list each supported release line here explicitly.

| Version                | Supported |
|------------------------|-----------|
| `main` (tip)           | yes       |
| older commits / forks  | no        |

## Reporting a Vulnerability

Please report vulnerabilities **privately** — do not open a public GitHub issue
for anything you believe is exploitable.

1. Email the maintainers at **security@example.com** (placeholder — replace with
   the real security contact address before relying on this policy).
   Include:
   - a description of the issue and its impact;
   - reproduction steps, PoC, or a minimal failing example;
   - affected commit hash or file paths.
2. You will receive an acknowledgement within **5 business days**.
3. We will coordinate a fix and disclosure timeline with you, and will credit
   you in the advisory unless you prefer to remain anonymous.

Please give us a reasonable window to ship a fix before any public disclosure.

## Scope Notes

This repository is an algorithmic trading system. Reports of the following are
especially valuable:

- anything that weakens the Risk Engine's sole-authority invariant (`_ISSUER`
  token checks on `ApprovedOrder`/`ApprovedExit`);
- secret leakage into the repo (CI runs gitleaks, currently in non-blocking
  mode — see `.github/workflows/deployment_validation.yml`);
- dependency vulnerabilities (CI runs pip-audit and trivy, also non-blocking).
