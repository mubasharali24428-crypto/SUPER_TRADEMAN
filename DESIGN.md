# DESIGN.md — SUPER_TRADEMAN Design System

## Personality
Crisp professional trading terminal. Dark, dense, calm. Motion is feedback, never decoration.

## Color tokens
| Token | Value | Use |
|---|---|---|
| `--bg-primary` | #07090e | page background |
| `--bg-secondary` | #0d111b | panels |
| `--glass-bg` | rgba(14,19,32,.65) | cards (hairline border, no gradient) |
| `--color-cyan` | #00f0ff | accent — one interactive accent, used sparingly |
| `--color-emerald` | #00ff88 | positive deltas only |
| `--color-red` | #ff3366 | negative deltas / errors only |

Rule: gradients are banned on surfaces. Depth comes from hairline borders
(rgba(255,255,255,0.06)) and elevation shadow, never color washes.

## Motion tokens (Emil Kowalski bar)
| Token | Value | Use |
|---|---|---|
| `--ease-out` | cubic-bezier(0.23, 1, 0.32, 1) | all entrances/exits |
| `--ease-in-out` | cubic-bezier(0.77, 0, 0.175, 1) | on-screen morphs |
| `--ease-drawer` | cubic-bezier(0.32, 0.72, 0, 1) | drawer/sheet slide |
| `--dur-fast` | 150ms | hover, press, small fades |
| `--dur-med` | 250ms | drawers, tabs, card entrances |

Rules: UI motion ≤300ms · ease-out for enter/exit (never ease-in) · transform+opacity
only (GPU) · :active scale(0.97) on every pressable · entrances start scale(0.95),
never scale(0) · stagger groups 50ms · reduced-motion keeps opacity, drops transforms ·
hover effects gated behind `(hover:hover) and (pointer:fine)`.

## Spacing scale
4 / 8 / 16 / 24 / 32 / 48 px (`--sp-1..6`). No arbitrary values.

## Radius scale
sm=6px (inputs, chips) · md=10px (buttons, small cards) · lg=14px (panels). Nothing else.

## Typography
- system-ui stack; tabular-nums (`font-variant-numeric`) on every numeric display.
- Headings ≥24px: letter-spacing -0.02em. Uppercase micro-labels: +0.01em.
- Hierarchy from weight + size + leading together, never size alone.
