#!/bin/bash
set -e
cd /app

###############################################################################
# 1. Modify hc/api/models.py — add imports, fields, and detect_anomaly method
###############################################################################

python3 << 'PATCH_MODELS'
import re

model_path = "/app/hc/api/models.py"

with open(model_path, "r") as f:
    content = f.read()

# ── 1a. Insert `import statistics` after the last `from __future__` line ──
# `from __future__` must be the very first statement; we must not prepend
# anything before it.
if "import statistics" not in content:
    import re
    # Find the end of all `from __future__` lines (there may be one or more)
    future_match = None
    for m in re.finditer(r'^from __future__ import [^\n]+\n', content, re.MULTILINE):
        future_match = m
    if future_match:
        insert_pos = future_match.end()
        content = content[:insert_pos] + "import statistics\n" + content[insert_pos:]
    else:
        # No __future__ import – safe to prepend
        content = "import statistics\n" + content

# ── 1b. Add fields to Check model ─────────────────────────────────────────
# The field in the file is indented (it lives inside the class body).
# We search for the EXACT indented string so we don't double-indent.
field_marker = "    n_pings = models.IntegerField(default=0)"
new_fields = (
    "    n_pings = models.IntegerField(default=0)\n"
    "    anomaly_score = models.FloatField(default=0.0)\n"
    "    is_anomalous = models.BooleanField(default=False)"
)

if "anomaly_score" not in content:
    if field_marker in content:
        content = content.replace(field_marker, new_fields, 1)
    else:
        # Fallback: insert after first class field line inside Check
        # by appending after `class Check(models.Model):`
        content = content.replace(
            "class Check(models.Model):",
            "class Check(models.Model):\n"
            "    anomaly_score = models.FloatField(default=0.0)\n"
            "    is_anomalous = models.BooleanField(default=False)",
            1,
        )

# ── 1c. Add detect_anomaly method ─────────────────────────────────────────
method_code = '''
    def detect_anomaly(self):
        """Compute z-score of the latest ping interval; flag anomalies."""
        # Fetch up to 21 recent timestamps (gives at most 20 intervals)
        pings = list(
            self.ping_set.order_by("-created")
            .values_list("created", flat=True)[:21]
        )

        # Need ≥6 pings (5 intervals) for meaningful statistics
        if len(pings) < 6:
            if self.anomaly_score != 0.0 or self.is_anomalous:
                self.anomaly_score = 0.0
                self.is_anomalous = False
                self.save(update_fields=["anomaly_score", "is_anomalous"])
            return

        # Compute time deltas (seconds) between consecutive pings (newest first)
        intervals = [
            (pings[i] - pings[i + 1]).total_seconds()
            for i in range(len(pings) - 1)
        ]

        mean = statistics.mean(intervals)
        stdev = statistics.stdev(intervals)   # sample stdev (ddof=1)

        current_interval = intervals[0]

        # Guard against zero variance (perfectly regular pings → no anomaly)
        if stdev == 0.0:
            z_score = 0.0
        else:
            z_score = (current_interval - mean) / stdev

        new_score = abs(z_score)
        is_anomalous = new_score > 3.0

        if self.anomaly_score != new_score or self.is_anomalous != is_anomalous:
            self.anomaly_score = new_score
            self.is_anomalous = is_anomalous
            self.save(update_fields=["anomaly_score", "is_anomalous"])
'''

if "def detect_anomaly(self):" not in content:
    # Prefer inserting just before prune(), otherwise before the first @property
    if "\n    def prune(self):" in content:
        content = content.replace(
            "\n    def prune(self):",
            method_code + "\n    def prune(self):",
            1,
        )
    else:
        # Insert before the first @property inside the Check class
        content = content.replace("\n    @property", method_code + "\n    @property", 1)

with open(model_path, "w") as f:
    f.write(content)

print("models.py patched OK")
PATCH_MODELS

###############################################################################
# 2. Modify hc/api/views.py — call detect_anomaly after check.ping()
###############################################################################

python3 << 'PATCH_VIEWS'
import re

view_path = "/app/hc/api/views.py"

with open(view_path, "r") as f:
    content = f.read()

if "detect_anomaly" not in content:
    # Match any line that is exactly a `check.ping(...)` call (possibly with
    # trailing whitespace).  Capture the leading indent so we can reuse it.
    # The call may span only one line in this codebase.
    def after_ping(m):
        indent = m.group(1)
        call    = m.group(2)
        return f"{indent}{call}\n{indent}check.detect_anomaly()\n"

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
