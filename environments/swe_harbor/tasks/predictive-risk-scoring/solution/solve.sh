#!/bin/bash
set -e
cd /app

###############################################################################
# 1. Modify hc/api/models.py — add risk_score field and compute_risk method
###############################################################################

python3 << 'PATCH_MODELS'
import re

model_path = "/app/hc/api/models.py"

with open(model_path, "r") as f:
    content = f.read()

# ── 1a. Add risk_score field after n_pings ────────────────────────────────
field_marker = "    n_pings = models.IntegerField(default=0)"
new_fields = (
    "    n_pings = models.IntegerField(default=0)\n"
    "    risk_score = models.FloatField(default=0.0)"
)

if "risk_score" not in content:
    if field_marker in content:
        content = content.replace(field_marker, new_fields, 1)
    else:
        # Fallback: insert after `class Check(models.Model):`
        content = content.replace(
            "class Check(models.Model):",
            "class Check(models.Model):\n    risk_score = models.FloatField(default=0.0)",
            1,
        )

# ── 1b. Add compute_risk method ───────────────────────────────────────────
method_code = '''
    def compute_risk(self):
        """Compute a probabilistic risk score [0.0, 1.0] from recent ping history.

        Combines three engineered features:
          1. recent_failure_ratio  (weight 0.40)
          2. interval_variability  (weight 0.35) — coefficient of variation
          3. latency_trend_score   (weight 0.25) — normalised OLS slope
        """
        WINDOW = 21  # fetch 21 to get 20 intervals

        pings = list(
            self.ping_set.order_by("-created")
            .values_list("created", "kind")[:WINDOW]
        )

        # Need at least 3 pings to produce any meaningful signals
        if len(pings) < 3:
            if self.risk_score != 0.0:
                self.risk_score = 0.0
                self.save(update_fields=["risk_score"])
            return

        # ── Feature 1: recent failure ratio ────────────────────────────────
        failure_count = sum(1 for _, k in pings if k == "fail")
        failure_ratio = failure_count / len(pings)

        # ── Feature 2: interval variability (CV = σ/μ) ─────────────────────
        intervals = [
            (pings[i][0] - pings[i + 1][0]).total_seconds()
            for i in range(len(pings) - 1)
            if pings[i][0] > pings[i + 1][0]
        ]

        variability_score = 0.0
        if len(intervals) >= 2:
            n = len(intervals)
            mean = sum(intervals) / n
            if mean > 0:
                variance = sum((x - mean) ** 2 for x in intervals) / n
                stdev = variance ** 0.5
                variability_score = min(stdev / mean, 1.0)

        # ── Feature 3: latency trend score (OLS slope, normalised) ─────────
        trend_score = 0.0
        if len(intervals) >= 3:
            # Oldest-first order so a positive slope means increasing intervals
            rev = list(reversed(intervals))
            n = len(rev)
            xs = list(range(n))
            sx = sum(xs)
            sy = sum(rev)
            sxy = sum(x * y for x, y in zip(xs, rev))
            sx2 = sum(x * x for x in xs)
            denom = n * sx2 - sx * sx
            if denom != 0:
                slope = (n * sxy - sx * sy) / denom
                mean_iv = sy / n
                if mean_iv > 0:
                    norm_slope = slope / mean_iv
                    # Only a *positive* (degrading) trend contributes
                    trend_score = max(0.0, min(norm_slope, 1.0))

        # ── Weighted combination ────────────────────────────────────────────
        risk = (
            0.40 * failure_ratio
            + 0.35 * variability_score
            + 0.25 * trend_score
        )
        risk = max(0.0, min(risk, 1.0))

        if self.risk_score != risk:
            self.risk_score = risk
            self.save(update_fields=["risk_score"])
'''

if "def compute_risk(self):" not in content:
    if "\n    def prune(self):" in content:
        content = content.replace(
            "\n    def prune(self):",
            method_code + "\n    def prune(self):",
            1,
        )
    else:
        content = content.replace("\n    @property", method_code + "\n    @property", 1)

with open(model_path, "w") as f:
    f.write(content)

print("models.py patched OK")
PATCH_MODELS

###############################################################################
# 2. Modify hc/api/views.py — call compute_risk after check.ping()
###############################################################################

python3 << 'PATCH_VIEWS'
import re

view_path = "/app/hc/api/views.py"

with open(view_path, "r") as f:
    content = f.read()

if "compute_risk" not in content:
    # Insert `check.compute_risk()` (in a try/except) immediately after every
    # `check.ping(...)` call, preserving the line's existing indentation.
    def after_ping(m):
        indent = m.group(1)
        call   = m.group(2)
        return (
            f"{indent}{call}\n"
            f"{indent}try:\n"
            f"{indent}    check.compute_risk()\n"
            f"{indent}except Exception:\n"
            f"{indent}    pass\n"
        )

    content = re.sub(
        r"^(\s*)(check\.ping\(.*\))\s*$",
        after_ping,
        content,
        flags=re.MULTILINE,
    )

    with open(view_path, "w") as f:
        f.write(content)

    print("views.py patched OK")
else:
    print("views.py already patched")
PATCH_VIEWS

###############################################################################
# 3. Migrations
###############################################################################

/app/manage.py makemigrations api
/app/manage.py migrate api
