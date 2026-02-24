# Anomaly Detection — Statistical Time-Series Reasoning (High)

The Anomaly Detection service should analyze recent ping intervals using rolling statistical measures (mean, standard deviation, z-score) to detect abnormal timing behavior. It must compute interval deltas from recent ping timestamps (e.g., last 20), calculate rolling statistics safely, handle edge cases (low sample size, zero variance), and persist anomaly state on the Check model (anomaly_score, is_anomalous). Development should begin by identifying the correct integration point in the ping processing flow, then implementing deterministic z-score logic with safe guards (no divide-by-zero, bounded window size). Optimization matters — only recent pings should be queried using .values_list() and limited slicing to avoid performance regressions. The expected outcome is deterministic, reproducible statistical behavior that correctly flags outliers while avoiding false positives under stable conditions. Tests must inject controlled timestamp sequences to validate stable, borderline, and extreme anomaly cases, ensuring the system cannot be bypassed with hardcoded thresholds.

## Requirements

### 1. Data Model Updates (`hc.api.models.Check`)
- **`anomaly_score`**: A FloatField (default=0.0) to store the latest calculated z-score magnitude.
- **`is_anomalous`**: A BooleanField (default=False) to flag if the check is currently behaving abnormally.

### 2. Anomaly Detection Logic (`Check.detect_anomaly`)
Implement a method `detect_anomaly(self)` on the `Check` model that performs the following steps:
1.  **Data Retrieval**: Fetch the `created` timestamps of the **last 20 pings** for this check, ordered by most recent first.
    -   Optimization: Use `.values_list('created', flat=True)` and slice `[:20]` to minimize database load.
2.  **Interval Calculation**: Compute the time duration (in seconds) between consecutive pings.
    -   Example: If pings are at T=100, T=90, T=80, intervals are [10, 10].
    -   If fewer than **5 intervals** (i.e., fewer than 6 pings) are available, the check cannot be evaluated. Reset `anomaly_score=0.0` and `is_anomalous=False`.
3.  **Statistical Profiling**:
    -   Calculate the **mean** ($\mu$) and **sample standard deviation** ($\sigma$) of the intervals.
    -   If $\sigma == 0$, assume no anomaly unless the current interval differs (but for this task, if $\sigma == 0$, result is 0.0 to avoid division by zero errors).
4.  **Z-Score Analysis**:
    -   The "current interval" is the time difference between the *most recent* ping and the one before it.
    -   Calculate $z = \frac{\text{current\_interval} - \mu}{\sigma}$.
    -   Store $|z|$ as `anomaly_score`.
5.  **Thresholding**:
    -   If `anomaly_score > 3.0` (i.e., more than 3 standard deviations from the mean), set `is_anomalous=True`.
    -   Otherwise, `is_anomalous=False`.
6.  **Persistence**: Save the `anomaly_score` and `is_anomalous` fields to the database.

### 3. Integration (`hc.api.views.ping`)
- Integrate `check.detect_anomaly()` into the ping processing flow in `hc/api/views.py`.
- Ensure it runs **after** the new ping is saved so the newest ping is included in the analysis.

## Verification
- Create a test case that simulates a series of regular pings (e.g., every 60 seconds) to establish a baseline.
- Inject an outlier ping (e.g., 600 seconds later) and verify `is_anomalous=True` and a high `anomaly_score`.
- Verify that a return to regular behavior clears the anomaly flag.
- Verify that checks with insufficient history are ignored.
- Verify zero-division safety (e.g., perfect 60s intervals then one 60s interval).

