# Predictive Risk Scoring — Multi-Signal Feature Aggregation (Very High)

Predictive Risk Scoring should compute a probabilistic risk indicator (0–1) estimating the likelihood of future failure using multiple engineered features derived from recent ping history. This service combines signals using a deterministic weighted formula with normalization and clamping to maintain numerical stability.

## Requirements

### 1. Data Model Update (`hc.api.models.Check`)
Add a single field to the `Check` model:
- **`risk_score`**: `FloatField(default=0.0)` — stores the latest computed risk value, always in the range [0.0, 1.0].

### 2. Feature Engineering (`Check.compute_risk`)
Implement a method `compute_risk(self)` on `Check` that:

1. **Data retrieval**: Fetch the last **20 pings** ordered by most recent first using `.values_list("created", "kind")[:21]` to stay within a bounded window.
2. **Minimum data guard**: If fewer than **3 pings** are available, reset `risk_score=0.0` and return early.
3. **Feature 1 — Recent Failure Ratio** (`w=0.40`):
   - Count pings where `kind == "fail"`, divide by total pings in window.
   - Range: [0.0, 1.0].
4. **Feature 2 — Interval Variability Score** (`w=0.35`):
   - Compute time deltas (seconds) between consecutive pings.
   - Calculate the **coefficient of variation** (CV = σ / μ) of intervals.
   - If mean is 0 or fewer than 2 intervals exist, score = 0.0.
   - Clamp result to [0.0, 1.0].
5. **Feature 3 — Latency Trend Score** (`w=0.25`):
   - Treat intervals in chronological order (oldest first within the window).
   - Compute the **linear regression slope** over the interval sequence using the closed-form OLS formula (no external libraries).
   - Normalize by the mean interval: `trend_score = slope / mean_interval`.
   - Clamp to [0.0, 1.0] — only a positive (degrading) trend contributes; negative (improving) slopes contribute 0.
   - If fewer than 3 intervals or denominator is 0, score = 0.0.
6. **Weighted combination**:
   ```
   risk = 0.40 * failure_ratio + 0.35 * variability_score + 0.25 * trend_score
   ```
   Clamp final result to [0.0, 1.0].
7. **Persistence**: Save `risk_score` only if the value changed, using `update_fields=["risk_score"]`.

### 3. Integration
- Call `check.compute_risk()` in `hc/api/views.py` after the `check.ping(...)` call in the main ping view.
- Use a `try/except Exception: pass` guard so risk computation never breaks a ping request.

### 4. Migration
- Generate and apply a migration for the new `risk_score` field.

## Behavioral Contract

| Scenario | Expected |
|---|---|
| Fewer than 3 pings | `risk_score == 0.0` |
| Stable 60s intervals, no failures | `risk_score < 0.10` |
| 100% failure rate | `risk_score >= 0.40` |
| Steadily increasing intervals | `risk_score` higher than stable baseline |
| Recovery (intervals normalize, no failures) | `risk_score` decreases |
| Extreme degradation + failures | `risk_score` close to 1.0 |
| Any input | `0.0 <= risk_score <= 1.0` |

## Constraints
- **No external libraries** — use only Python stdlib (`statistics` module allowed).
- Avoid loading full `Ping` objects; use `.values_list()` for efficiency.
- Risk computation must be **deterministic**: identical ping history → identical score.
