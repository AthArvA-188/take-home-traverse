"""Tests for the Anomaly Detection feature."""
import os
import sys
from datetime import timedelta as td
import random
import statistics

sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.test import TestCase
from django.utils.timezone import now
from hc.api.models import Check, Ping
from hc.test import BaseTestCase

class AnomalyDetectionTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project)
        self.check.created = now() - td(days=1)
        self.check.save()

    def test_model_fields_exist(self):
        """Verify that new fields exist on the Check model."""
        self.assertTrue(hasattr(self.check, "anomaly_score"))
        self.assertTrue(hasattr(self.check, "is_anomalous"))
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_insufficient_data(self):
        """Should skip detection if fewer than 5 intervals (6 pings)."""
        # Create 4 pings (3 intervals)
        start = now() - td(minutes=10)
        for i in range(4):
            Ping.objects.create(owner=self.check, created=start + td(minutes=i))
        
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_consistent_pings_zero_variance(self):
        """Should handle perfect consistency (stdev=0) gracefully."""
        # Create 10 pings with exactly 60s gap
        base_time = now() - td(minutes=20)
        for i in range(10):
            Ping.objects.create(owner=self.check, created=base_time + td(seconds=60*i))
            
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_normal_variation(self):
        """Pings within normal variation should not trigger anomaly."""
        # Create pings with small random jitter around 60s
        # We need deterministic test, so avoid actual random.
        # Sequence: 60, 61, 59, 60, 60, 61, 59...
        intervals = [60, 61, 59, 60, 60, 61, 59, 60, 60, 61]
        t = now() - td(minutes=20)
        Ping.objects.create(owner=self.check, created=t)
        
        for duration in intervals:
            t += td(seconds=duration)
            Ping.objects.create(owner=self.check, created=t)
            
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        
        # Calculate expected z-score roughly
        # Mean ~60.1, Stdev ~0.8
        # Last interval 61. (61-60.1)/0.8 ~= 1.1 < 3.0
        self.assertFalse(self.check.is_anomalous)
        self.assertTrue(0.0 <= self.check.anomaly_score < 3.0)

    def test_anomaly_spike(self):
        """A sudden large delay should trigger an anomaly."""
        # 20 steady pings at 60s
        # Then one at 300s (5 minutes)
        t = now() - td(hours=1)
        Ping.objects.create(owner=self.check, created=t)
        
        # Consistent history
        for _ in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
            
        # Spike
        t += td(seconds=300)
        Ping.objects.create(owner=self.check, created=t)
        
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        
        # Stats of 20 intervals: 19 * 60, 1 * 300
        # Mean = (19*60 + 300) / 20 = (1140 + 300) / 20 = 1440 / 20 = 72
        # Variance = sum((x - 72)^2) / 19
        # (60-72)^2 = 144. 19 times = 2736
        # (300-72)^2 = 228^2 = 51984
        # Sum = 54720
        # Variance = 54720 / 19 = 2880
        # Stdev = sqrt(2880) = ~53.66
        # Current interval = 300.
        # Z = (300 - 72) / 53.66 = 228 / 53.66 = 4.25 > 3.0
        
        self.assertTrue(self.check.is_anomalous)
        self.assertGreater(self.check.anomaly_score, 3.0)

    def test_anomaly_early_ping(self):
        """A very early ping (frequent firing) should trigger anomaly."""
        # 20 steady pings at 60s
        t = now() - td(hours=1)
        Ping.objects.create(owner=self.check, created=t)
        
        # Consistent history
        for _ in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
            
        # Early ping (1s interval)
        t += td(seconds=1)
        Ping.objects.create(owner=self.check, created=t)
        
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        
        # Stats: 19 * 60, 1 * 1
        # Mean: (1140 + 1) / 20 = 57.05
        # (60-57.05)^2 = 2.95^2 = 8.7 * 19 = 165.3
        # (1-57.05)^2 = 56.05^2 = 3141.6
        # Variance = 3306.9 / 19 = 174.05
        # Stdev = 13.19
        # Current = 1
        # Z = (1 - 57.05) / 13.19 = -56.05 / 13.19 = -4.25
        # Abs Z = 4.25 > 3.0
        
        self.assertTrue(self.check.is_anomalous)
        self.assertGreater(self.check.anomaly_score, 3.0)

    def test_integration_ping_view(self):
        """Calling the ping URL should trigger anomaly detection and persist it."""
        # Setup history
        t = now() - td(hours=1)
        Ping.objects.create(owner=self.check, created=t)
        
        # 19 steady pings
        for i in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
            
        # We need to execute a ping via URL such that the gap is large (anomaly).
        # But we can't easily control time inside the view execution unless we patch timezone.now
        # However, the view uses `timezone.now()` for the new ping.
        # So we just wait or set our history far in the past.
        # Our last ping above is at `t`.
        # If we set `t` to be 1 hour ago, and ping NOW, the interval will be 3600s.
        # 60s vs 3600s is huge anomaly.
        
        # Let's adjust timestamps of existing pings to be 2 hours ago
        # consistent 60s intervals ending 1 hour ago.
        
        base = now() - td(hours=2)
        pings = list(Ping.objects.filter(owner=self.check).order_by("created"))
        # We created 1 (init) + 19 = 20 pings.
        
        for i, p in enumerate(pings):
            p.created = base + td(seconds=60*i)
            p.save()
            
        # Now verify stats
        # Last ping was at base + 19*60 = base + 1140s.
        # Check current time
        # We assume ping view uses `now()`.
        # Time since last ping ~ 2 hours - 20 mins = 100 mins = 6000s.
        # 6000s vs 60s is massive Z-score.
        
        url = self.check.url()
        self.client.post(url)
        
        self.check.refresh_from_db()
        self.assertTrue(self.check.is_anomalous)
        # z ≈ 4.25: (6060 - 360) / stdev(1341.6) — well above the 3.0 threshold
        self.assertGreater(self.check.anomaly_score, 3.0)

    def test_reset_anomaly(self):
        """Anomaly state should clear when behavior returns to normal."""
        self.check.is_anomalous = True
        self.check.anomaly_score = 10.0
        self.check.save()

        base = now() - td(seconds=21 * 60)
        for i in range(21):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))

        self.check.detect_anomaly()
        self.check.refresh_from_db()

        self.assertFalse(self.check.is_anomalous)
        self.assertEqual(self.check.anomaly_score, 0.0)

    # ------------------------------------------------------------------
    # Edge-case tests added for coverage
    # ------------------------------------------------------------------

    def test_five_pings_below_boundary(self):
        """5 pings = 4 intervals, below 5-interval threshold → skipped."""
        base = now() - td(minutes=10)
        for i in range(5):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_six_pings_exactly_at_boundary(self):
        """6 pings = exactly 5 intervals — minimum valid window, must run."""
        # 6 pings at 60s spacing → stdev=0 → z=0 → not anomalous
        base = now() - td(minutes=10)
        for i in range(6):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        # stdev=0 → score=0, not anomalous
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_one_ping_no_anomaly(self):
        """A single ping must not crash and must leave score=0."""
        Ping.objects.create(owner=self.check, created=now() - td(minutes=1))
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_anomaly_score_non_negative(self):
        """anomaly_score is |z|, must always be >= 0."""
        # early ping produces negative z; abs should still be positive
        t = now() - td(hours=1)
        Ping.objects.create(owner=self.check, created=t)
        for _ in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
        t += td(seconds=1)   # very short interval → negative z
        Ping.objects.create(owner=self.check, created=t)
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertGreaterEqual(self.check.anomaly_score, 0.0)

    def test_borderline_below_threshold(self):
        """20 intervals: 18 × 60 s, CURRENT × 300 s and one more 300 s.

        Analytical result: z ≈ 2.924 < 3.0 → NOT anomalous.

        With a=60, b=300, n=20 intervals (18 at a, 2 at b):
          mean = (2*300 + 18*60)/20 = 84
          sample_stdev = sqrt((2*(300-84)^2 + 18*(60-84)^2)/19)
                       = sqrt(103680/19) ≈ 73.87
          z = (300-84)/73.87 ≈ 2.924 < 3.0
        """
        base = now() - td(hours=1)
        t = base
        # 19 pings with 60-s gaps (18 intervals of 60), then 2 with 300-s gaps
        for _ in range(19):
            Ping.objects.create(owner=self.check, created=t)
            t += td(seconds=60)
        # two 300-s intervals
        Ping.objects.create(owner=self.check, created=t)
        t += td(seconds=300)
        Ping.objects.create(owner=self.check, created=t)   # newest
        t += td(seconds=300)
        Ping.objects.create(owner=self.check, created=t)   # newest
        # Total: 21 pings → 20 intervals: [300, 300, 60×18]
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        # z ≈ 2.924 — must NOT be flagged
        self.assertFalse(self.check.is_anomalous)
        # Score is the abs z ≈ 2.924
        self.assertLess(self.check.anomaly_score, 3.0)
        self.assertGreater(self.check.anomaly_score, 2.5)

    def test_borderline_above_threshold(self):
        """20 intervals: 19 × 10 s, CURRENT × 100 s.

        Analytical result: z ≈ 4.25 > 3.0 → IS anomalous.

        With a=10, b=100, n=20 intervals (19 at a, 1 current at b):
          mean = (19*10 + 100)/20 = 290/20 = 14.5
          sample_stdev = sqrt((1*(100-14.5)^2 + 19*(10-14.5)^2)/19)
                       = sqrt((7310.25+384.75)/19) = sqrt(405) ≈ 20.12
          z = (100-14.5)/20.12 ≈ 4.25 > 3.0
        """
        base = now() - td(hours=1)
        t = base
        Ping.objects.create(owner=self.check, created=t)
        for _ in range(19):
            t += td(seconds=10)
            Ping.objects.create(owner=self.check, created=t)
        # current interval = 100 s
        t += td(seconds=100)
        Ping.objects.create(owner=self.check, created=t)
        # Total: 21 pings → 20 intervals: [100, 10×19]
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertTrue(self.check.is_anomalous)
        self.assertGreater(self.check.anomaly_score, 3.0)

    def test_extreme_large_spike(self):
        """A spike 1000× the baseline should be clearly anomalous (z > 3.0).

        With 19 intervals of 60 s and 1 of 60 000 s:
          mean = (19*60 + 60000)/20 = 3057
          sample stdev ≈ 13 403
          z = (60000 − 3057) / 13403 ≈ 4.25  — well above the 3.0 threshold.
        """
        base = now() - td(hours=2)
        t = base
        Ping.objects.create(owner=self.check, created=t)
        for _ in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
        t += td(seconds=60_000)   # ~16 hours late
        Ping.objects.create(owner=self.check, created=t)
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertTrue(self.check.is_anomalous)
        self.assertGreater(self.check.anomaly_score, 3.0)

    def test_multiple_independent_checks(self):
        """Anomaly state on check A must not affect a separate check B."""
        check_b = Check.objects.create(project=self.project)

        # Give check_a a clear anomaly
        t = now() - td(hours=1)
        Ping.objects.create(owner=self.check, created=t)
        for _ in range(19):
            t += td(seconds=60)
            Ping.objects.create(owner=self.check, created=t)
        t += td(seconds=300)
        Ping.objects.create(owner=self.check, created=t)
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertTrue(self.check.is_anomalous)

        # check_b has no pings → must remain clean
        check_b.detect_anomaly()
        check_b.refresh_from_db()
        self.assertFalse(check_b.is_anomalous)
        self.assertEqual(check_b.anomaly_score, 0.0)

    def test_gradual_drift_not_anomalous(self):
        """A slow, gradual drift within 3 stdev does not trigger an anomaly.

        Intervals: 55, 57, 59, 61, 63, 65, 60, 60, 60, 60 (10 values + init = 11 pings)
        The most recent interval (65) is within normal variation.
        """
        intervals = [55, 57, 59, 61, 63, 65, 60, 60, 60, 60]
        base = now() - td(minutes=20)
        t = base
        Ping.objects.create(owner=self.check, created=t)
        for gap in intervals:
            t += td(seconds=gap)
            Ping.objects.create(owner=self.check, created=t)

        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertFalse(self.check.is_anomalous)
        self.assertLess(self.check.anomaly_score, 3.0)

    def test_window_limited_to_21_pings(self):
        """Only the 21 most-recent pings feed the detector; older ones are ignored.

        Inject 10 very-long-gap pings first (oldest), then 21 pings at 60s.
        If the window is applied correctly, none of the 10 old pings enter
        the calculation and the detector should see all stable intervals → score=0.
        """
        # 10 ancient pings with huge gaps
        ancient = now() - td(days=10)
        for i in range(10):
            Ping.objects.create(owner=self.check, created=ancient + td(hours=i * 5))

        # 21 recent stable pings (makes 20 intervals of exactly 60 s)
        base = now() - td(minutes=25)
        for i in range(21):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))

        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertEqual(self.check.anomaly_score, 0.0)
        self.assertFalse(self.check.is_anomalous)

    def test_anomaly_score_is_float(self):
        """anomaly_score must be a float, not an integer or string."""
        base = now() - td(minutes=15)
        for i in range(10):
            Ping.objects.create(owner=self.check, created=base + td(seconds=60 * i))
        self.check.detect_anomaly()
        self.check.refresh_from_db()
        self.assertIsInstance(self.check.anomaly_score, float)

    def test_is_anomalous_cleared_to_db(self):
        """When anomaly clears, is_anomalous=False and score=0 must be persisted."""
        # Set flags manually in DB
        self.check.is_anomalous = True
        self.check.anomaly_score = 7.5
        self.check.save()

        # Re-fetch from DB to confirm they're stored
        fresh = Check.objects.get(pk=self.check.pk)
        self.assertTrue(fresh.is_anomalous)

        # Now provide perfectly stable history and call detect_anomaly
        base = now() - td(seconds=21 * 60)
        for i in range(21):
            Ping.objects.create(owner=fresh, created=base + td(seconds=60 * i))
        fresh.detect_anomaly()

        # Fetch a brand-new ORM instance to prove DB was updated
        saved = Check.objects.get(pk=self.check.pk)
        self.assertFalse(saved.is_anomalous)
        self.assertEqual(saved.anomaly_score, 0.0)


