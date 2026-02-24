"""Tests for the Predictive Risk Scoring feature.

All expected numeric values are computed analytically; no wall-clock jitter
is permitted in the model-level tests (compute_risk is called directly).
"""
import os
import sys
import math
from datetime import timedelta as td

sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.utils.timezone import now
from hc.api.models import Check, Ping
from hc.test import BaseTestCase


def _make_pings(check, intervals, kind=None):
    """Create pings at base (now()-total_span) with given second-gaps.

    Returns the list of created Ping objects.
    """
    total = sum(intervals)
    base = now() - td(seconds=total + 60)
    t = base
    pings = [Ping.objects.create(owner=check, created=t, kind=kind)]
    for gap in intervals:
        t += td(seconds=gap)
        pings.append(Ping.objects.create(owner=check, created=t, kind=kind))
    return pings


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class PredictiveRiskScoringTestCase(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project)

    # ── 1. Field existence ──────────────────────────────────────────────────

    def test_model_field_exists(self):
        """risk_score field must exist on Check and default to 0.0."""
        self.assertTrue(hasattr(self.check, "risk_score"))
        self.assertEqual(self.check.risk_score, 0.0)

    # ── 2. No pings ────────────────────────────────────────────────────────

    def test_no_pings_zero_risk(self):
        """Zero pings → risk_score stays 0.0."""
        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertEqual(self.check.risk_score, 0.0)

    # ── 3. Insufficient pings ──────────────────────────────────────────────

    def test_insufficient_pings_zero_risk(self):
        """Fewer than 3 pings → risk_score is reset to 0.0."""
        base = now() - td(minutes=5)
        Ping.objects.create(owner=self.check, created=base)
        Ping.objects.create(owner=self.check, created=base + td(seconds=60))
        # Force a non-zero value to verify it resets
        self.check.risk_score = 0.99
        self.check.save()

        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertEqual(self.check.risk_score, 0.0)

    # ── 4. Perfectly stable pings ──────────────────────────────────────────

    def test_stable_exact_zero(self):
        """20 pings at exactly 60-s spacing, no failures → risk_score == 0.0.

        CV = 0 (identical intervals), OLS slope = 0, failure_ratio = 0.
        """
        _make_pings(self.check, [60] * 19)   # 20 pings

        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertEqual(self.check.risk_score, 0.0)

    # ── 5. All failures, constant interval ─────────────────────────────────

    def test_all_failures_constant_intervals(self):
        """20 failure pings at 60-s spacing.

        failure_ratio = 1.0, variability = 0, trend = 0
        → risk = 0.40 * 1.0 = 0.40 exactly.
        """
        _make_pings(self.check, [60] * 19, kind="fail")

        self.check.compute_risk()
        self.check.refresh_from_db()

        self.assertAlmostEqual(self.check.risk_score, 0.40, places=10)

    # ── 6. Degradation raises risk ─────────────────────────────────────────

    def test_degradation_raises_risk_above_stable(self):
        """Monotonically increasing intervals should produce higher risk than
        a perfectly stable baseline.

        Arithmetic progression: intervals = [60, 90, 120, ..., 600] (step 30)
        → 20 pings, 19 intervals, no failures.
        Analytically:
          mean = 330, pop_stdev ≈ 164.32, CV ≈ 0.498
          OLS slope = 30, trend_score = 30/330 ≈ 0.091
          risk ≈ 0.35*0.498 + 0.25*0.091 ≈ 0.197
        """
        # Build escalating timestamps: start offset such that end is ~60s ago
        gaps = [60 + 30 * i for i in range(19)]   # 60, 90, 120, ..., 600
        _make_pings(self.check, gaps)

        self.check.compute_risk()
        self.check.refresh_from_db()
        degraded_score = self.check.risk_score

        # Must be strictly above the stable-system risk
        self.assertGreater(degraded_score, 0.0)
        # Sanity check on expected range
        self.assertGreater(degraded_score, 0.10)
        self.assertLess(degraded_score, 1.0)

    # ── 7. Recovery lowers risk ────────────────────────────────────────────

    def test_recovery_lowers_risk(self):
        """After degradation, restoring stable pings should reduce risk."""
        # Phase 1 – degraded
        gaps = [60 + 30 * i for i in range(19)]
        _make_pings(self.check, gaps)
        self.check.compute_risk()
        self.check.refresh_from_db()
        degraded_score = self.check.risk_score

        # Phase 2 – recover: delete old pings, install stable ones
        Ping.objects.filter(owner=self.check).delete()
        _make_pings(self.check, [60] * 19)
        self.check.compute_risk()
        self.check.refresh_from_db()
        recovered_score = self.check.risk_score

        self.assertLess(recovered_score, degraded_score)
        self.assertEqual(recovered_score, 0.0)

    # ── 8. Extreme case — high risk ─────────────────────────────────────────

    def test_extreme_high_risk(self):
        """All failures + highly variable (alternating 10 s / 200 s) intervals.

        Analytically:
          20 pings → 19 intervals starting with 10s: [10,200,10,...,10]
          (10 × 10s + 9 × 200s)
          mean = 100.0, pop_stdev ≈ 94.87, CV ≈ 0.9487 → variability ≈ 0.9487
          OLS slope = 0 (perfectly alternating → zero slope)
          risk = 0.40*1 + 0.35*0.9487 + 0.25*0 ≈ 0.732
        """
        # Build alternating gaps: [10, 200, 10, 200, ...]  (19 gaps)
        gaps = []
        for i in range(19):
            gaps.append(10 if i % 2 == 0 else 200)

        _make_pings(self.check, gaps, kind="fail")

        self.check.compute_risk()
        self.check.refresh_from_db()

        # Analytically ≈ 0.732; assert well above 0.60 with comfortable margin
        self.assertGreater(self.check.risk_score, 0.60)
        self.assertLessEqual(self.check.risk_score, 1.0)

    # ── 9. Output always in [0, 1] ─────────────────────────────────────────

    def test_risk_score_clamped_to_unit_interval(self):
        """risk_score must always lie in [0.0, 1.0] regardless of input."""
        # Use extreme inputs: all failures + wildly varying intervals
        gaps = [1, 10000] * 9 + [1]    # alternating, 19 gaps
        _make_pings(self.check, gaps, kind="fail")

        self.check.compute_risk()
        self.check.refresh_from_db()

        self.assertGreaterEqual(self.check.risk_score, 0.0)
        self.assertLessEqual(self.check.risk_score, 1.0)

    # ── 10. Determinism ────────────────────────────────────────────────────

    def test_compute_risk_is_deterministic(self):
        """Same ping history must produce identical risk_score on every call."""
        gaps = [60 + 30 * i for i in range(19)]
        _make_pings(self.check, gaps)

        self.check.compute_risk()
        self.check.refresh_from_db()
        score_first = self.check.risk_score

        # Force recompute by resetting risk_score
        self.check.risk_score = 0.0
        self.check.save(update_fields=["risk_score"])

        self.check.compute_risk()
        self.check.refresh_from_db()
        score_second = self.check.risk_score

        self.assertEqual(score_first, score_second)

    # ── 11. Integration — ping view triggers compute_risk ──────────────────

    def test_integration_ping_view_updates_risk(self):
        """An HTTP ping should persist a non-zero risk_score when history shows
        failures.

        Setup: 19 pre-existing failure pings (so failure_ratio is high after
        the URL ping adds a 20th ping).  The request ping itself is a success,
        but the 19 previous failures dominate.
        """
        # 19 failure pings at 60-s spacing
        base = now() - td(minutes=25)
        t = base
        for i in range(19):
            Ping.objects.create(owner=self.check, created=t, kind="fail")
            t += td(seconds=60)

        url = self.check.url()
        self.client.post(url)

        self.check.refresh_from_db()
        # failure_ratio ≈ 19/20 = 0.95 → floor contribution ≈ 0.38
        # risk must be > 0.0 and persist in DB
        self.assertGreater(self.check.risk_score, 0.0)
        self.assertLessEqual(self.check.risk_score, 1.0)

    # ── 12. Mixed signals: partial failure + stable ─────────────────────────

    def test_partial_failure_moderate_risk(self):
        """5 failures out of 20 pings (25%), constant intervals → risk ≈ 0.10."""
        base = now() - td(minutes=25)
        t = base
        for i in range(20):
            kind = "fail" if i < 5 else None
            Ping.objects.create(owner=self.check, created=t, kind=kind)
            t += td(seconds=60)

        self.check.compute_risk()
        self.check.refresh_from_db()

        # failure_ratio = 5/20 = 0.25 → contribution = 0.10
        # variability = 0.0, trend = 0.0 → risk = 0.10 exactly
        self.assertAlmostEqual(self.check.risk_score, 0.10, places=10)

    # ------------------------------------------------------------------
    # Edge-case tests added for coverage
    # ------------------------------------------------------------------

    def test_exactly_three_pings_minimum_valid(self):
        """3 pings = 2 intervals — meets the 3-ping minimum; must not reset."""
        base = now() - td(minutes=5)
        # 3 pings at 60-s spacing, no failures → risk = 0.0
        for i in range(3):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))

        self.check.compute_risk()
        self.check.refresh_from_db()
        # No failures, identical intervals → risk = 0.0
        self.assertEqual(self.check.risk_score, 0.0)

    def test_two_pings_resets_nonzero_score(self):
        """2 pings (below minimum) must reset a pre-existing risk_score to 0."""
        base = now() - td(minutes=5)
        Ping.objects.create(owner=self.check, created=base)
        Ping.objects.create(owner=self.check, created=base + td(seconds=60))
        self.check.risk_score = 0.75
        self.check.save()

        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertEqual(self.check.risk_score, 0.0)

    def test_single_failure_exact_risk(self):
        """Exactly 1 failure in 20 pings with constant intervals → risk = 0.02.

        failure_ratio = 1/20 = 0.05 → 0.40 * 0.05 = 0.02.
        variability = 0 (stdev=0), trend = 0.
        """
        base = now() - td(minutes=25)
        t = base
        for i in range(20):
            kind = "fail" if i == 0 else None
            Ping.objects.create(owner=self.check, created=t, kind=kind)
            t += td(seconds=60)

        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertAlmostEqual(self.check.risk_score, 0.02, places=10)

    def test_window_capped_at_20_intervals(self):
        """Only the 20 most-recent intervals are used; older pings are ignored.

        Create 10 failure pings far in the past, then 21 stable success pings.
        The window should exclude the failures → risk = 0.0.
        """
        # 10 old failure pings (outside the 21-ping window)
        ancient = now() - td(days=5)
        for i in range(10):
            Ping.objects.create(owner=self.check, created=ancient + td(hours=i), kind="fail")

        # 21 recent stable success pings (20 intervals of 60 s)
        base = now() - td(minutes=25)
        for i in range(21):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))

        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertEqual(self.check.risk_score, 0.0)

    def test_independent_checks_do_not_share_scores(self):
        """risk_score on check A must not affect a separate check B."""
        check_b = Check.objects.create(project=self.project)

        # Give check_a high risk (all failures)
        _make_pings(self.check, [60] * 19, kind="fail")
        self.check.compute_risk()
        self.check.refresh_from_db()
        self.assertGreater(self.check.risk_score, 0.0)

        # check_b has no pings → must stay 0.0
        check_b.compute_risk()
        check_b.refresh_from_db()
        self.assertEqual(check_b.risk_score, 0.0)

    def test_risk_persists_to_db(self):
        """After compute_risk(), the score must survive a fresh DB fetch."""
        _make_pings(self.check, [60] * 19, kind="fail")
        self.check.compute_risk()
        in_memory = self.check.risk_score

        fresh = Check.objects.get(pk=self.check.pk)
        self.assertAlmostEqual(fresh.risk_score, in_memory, places=10)
        self.assertGreater(fresh.risk_score, 0.0)

    def test_negative_trend_not_penalised(self):
        """Improving (decreasing) intervals must NOT increase risk above variability.

        trend_score is clamped to max(0.0, normalised_slope), so a negative
        OLS slope contributes 0.  risk = 0.35 * variability only.
        """
        # Strictly decreasing gaps: 300, 270, 240, ..., 60  (step -30, 9 steps)
        # then stable at 60 for the rest to give enough intervals
        gaps = list(range(300, 50, -30))[:19]   # [300,270,...,60] – 9 values
        # pad to 19 with 60s
        gaps = gaps + [60] * (19 - len(gaps))
        _make_pings(self.check, gaps)

        self.check.compute_risk()
        self.check.refresh_from_db()

        # trend contribution must be 0; risk driven only by variability
        # If slope were allowed to go negative, score would be LOWER than
        # variability alone — we assert it's non-negative.
        self.assertGreaterEqual(self.check.risk_score, 0.0)
        self.assertLessEqual(self.check.risk_score, 1.0)
        # No failures → risk must be < 0.40
        self.assertLess(self.check.risk_score, 0.40)

    def test_ign_pings_not_counted_as_failures(self):
        """Pings with kind='ign' must not inflate the failure ratio."""
        # 10 'ign' pings + 10 success pings, all 60-s apart, no failures
        base = now() - td(minutes=25)
        t = base
        for i in range(20):
            kind = "ign" if i < 10 else None
            Ping.objects.create(owner=self.check, created=t, kind=kind)
            t += td(seconds=60)

        self.check.compute_risk()
        self.check.refresh_from_db()

        # failure_ratio = 0 → risk = 0 (constant intervals, no OLS slope)
        self.assertEqual(self.check.risk_score, 0.0)

    def test_monotonic_risk_increase_with_more_failures(self):
        """Adding more failures one by one should increase (or hold) risk score."""
        scores = []
        base = now() - td(minutes=25)

        for fail_count in [0, 5, 10, 19]:
            Ping.objects.filter(owner=self.check).delete()
            t = base
            for i in range(20):
                kind = "fail" if i < fail_count else None
                Ping.objects.create(owner=self.check, created=t, kind=kind)
                t += td(seconds=60)
            self.check.compute_risk()
            self.check.refresh_from_db()
            scores.append(self.check.risk_score)

        # risk must be non-decreasing as failures increase
        for i in range(len(scores) - 1):
            self.assertLessEqual(scores[i], scores[i + 1])
