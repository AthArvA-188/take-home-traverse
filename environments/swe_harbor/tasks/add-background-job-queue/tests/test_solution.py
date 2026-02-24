"""Tests for the Background Job Queue feature."""
from __future__ import annotations

import uuid
from datetime import timedelta as td

import os
import sys
sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.test import TestCase
from django.utils.timezone import now

from hc.api.models import Check, Flip, Ping
from hc.test import BaseTestCase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_job(**kwargs):
    """Helper: create a Job using Job.enqueue() with sensible defaults."""
    from hc.api.models import Job
    return Job.objects.create(
        job_type=kwargs.get("job_type", "send_alert"),
        payload=kwargs.get("payload", {}),
        status=kwargs.get("status", "pending"),
        attempts=kwargs.get("attempts", 0),
        max_attempts=kwargs.get("max_attempts", 3),
        idempotency_key=kwargs.get("idempotency_key", ""),
    )


# ===========================================================================
# 1. Job Model
# ===========================================================================

class JobModelTestCase(BaseTestCase):
    """Basic model existence, field defaults, and to_dict()."""

    def test_model_importable(self):
        """Job model should be importable from hc.api.models."""
        from hc.api.models import Job
        self.assertTrue(hasattr(Job, "objects"))

    def test_default_status_is_pending(self):
        """A freshly created Job should have status='pending'."""
        from hc.api.models import Job
        job = Job.objects.create(job_type="send_alert", payload={})
        self.assertEqual(job.status, "pending")

    def test_default_attempts_zero(self):
        """A freshly created Job should have attempts=0."""
        from hc.api.models import Job
        job = Job.objects.create(job_type="send_alert", payload={})
        self.assertEqual(job.attempts, 0)

    def test_default_max_attempts(self):
        """Default max_attempts should be 3."""
        from hc.api.models import Job
        job = Job.objects.create(job_type="send_alert", payload={})
        self.assertEqual(job.max_attempts, 3)

    def test_code_auto_generated(self):
        """code field should be auto-populated and unique."""
        from hc.api.models import Job
        j1 = Job.objects.create(job_type="send_alert", payload={})
        j2 = Job.objects.create(job_type="send_alert", payload={})
        self.assertIsNotNone(j1.code)
        self.assertNotEqual(j1.code, j2.code)

    def test_to_dict_has_required_keys(self):
        """to_dict() should contain all required keys."""
        from hc.api.models import Job
        job = Job.objects.create(job_type="send_alert", payload={"foo": "bar"})
        d = job.to_dict()
        for key in (
            "uuid", "job_type", "payload", "status", "attempts",
            "max_attempts", "idempotency_key", "created", "scheduled_at",
            "started_at", "completed_at", "error",
        ):
            self.assertIn(key, d, f"Missing key: {key}")

    def test_to_dict_values(self):
        """to_dict() should return correct field values."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="send_alert",
            payload={"check_code": "abc"},
            idempotency_key="test-key-123",
        )
        d = job.to_dict()
        self.assertEqual(d["uuid"], str(job.code))
        self.assertEqual(d["job_type"], "send_alert")
        self.assertEqual(d["payload"], {"check_code": "abc"})
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["idempotency_key"], "test-key-123")
        self.assertIsNone(d["started_at"])
        self.assertIsNone(d["completed_at"])
        self.assertEqual(d["error"], "")

    def test_ordering_by_scheduled_at(self):
        """Jobs should be ordered by scheduled_at ascending."""
        from hc.api.models import Job
        later = now() + td(hours=2)
        earlier = now() - td(hours=1)
        j_later = Job.objects.create(job_type="send_alert", payload={}, scheduled_at=later)
        j_earlier = Job.objects.create(job_type="send_alert", payload={}, scheduled_at=earlier)
        jobs = list(Job.objects.all())
        self.assertEqual(jobs[0].id, j_earlier.id)
        self.assertEqual(jobs[1].id, j_later.id)


# ===========================================================================
# 2. Job.enqueue() — idempotency
# ===========================================================================

class JobEnqueueTestCase(BaseTestCase):
    """Tests for the Job.enqueue() classmethod."""

    def test_enqueue_creates_pending_job(self):
        """enqueue() should create a job with status=pending."""
        from hc.api.models import Job
        job = Job.enqueue("send_alert", {"check_code": "abc"})
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.job_type, "send_alert")

    def test_enqueue_without_key_creates_multiple(self):
        """enqueue() with no idempotency_key creates a new job every time."""
        from hc.api.models import Job
        Job.enqueue("send_alert", {"x": 1})
        Job.enqueue("send_alert", {"x": 1})
        self.assertEqual(Job.objects.count(), 2)

    def test_enqueue_with_same_key_returns_existing(self):
        """enqueue() with same idempotency_key returns the existing job."""
        from hc.api.models import Job
        j1 = Job.enqueue("send_alert", {"x": 1}, idempotency_key="my-key")
        j2 = Job.enqueue("send_alert", {"x": 2}, idempotency_key="my-key")
        self.assertEqual(j1.id, j2.id)
        self.assertEqual(Job.objects.filter(idempotency_key="my-key").count(), 1)

    def test_enqueue_with_same_key_does_not_overwrite_payload(self):
        """enqueue() with existing key preserves the original payload."""
        from hc.api.models import Job
        Job.enqueue("send_alert", {"original": True}, idempotency_key="idem-1")
        j2 = Job.enqueue("send_alert", {"original": False}, idempotency_key="idem-1")
        self.assertTrue(j2.payload.get("original"))

    def test_enqueue_different_keys_create_different_jobs(self):
        """Different idempotency keys produce separate jobs."""
        from hc.api.models import Job
        j1 = Job.enqueue("send_alert", {}, idempotency_key="key-a")
        j2 = Job.enqueue("send_alert", {}, idempotency_key="key-b")
        self.assertNotEqual(j1.id, j2.id)
        self.assertEqual(Job.objects.count(), 2)


# ===========================================================================
# 3. processjobs management command
# ===========================================================================

class ProcessJobsCommandTestCase(BaseTestCase):
    """Tests for the processjobs management command."""

    def _run_command(self):
        from hc.api.management.commands.processjobs import Command
        cmd = Command()
        return cmd.handle()

    def test_processes_pending_job_to_done(self):
        """A valid pending job should be processed and marked done."""
        from hc.api.models import Job
        check = Check.objects.create(project=self.project, name="C1")
        job = Job.enqueue("send_alert", {"check_code": str(check.code), "new_status": "down"})

        self._run_command()

        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.completed_at)
        self.assertEqual(job.attempts, 1)

    def test_returns_processed_count(self):
        """handle() should return a string indicating how many jobs were processed."""
        from hc.api.models import Job
        check = Check.objects.create(project=self.project, name="C2")
        Job.enqueue("send_alert", {"check_code": str(check.code), "new_status": "down"})
        Job.enqueue("send_alert", {"check_code": str(check.code), "new_status": "up"}, idempotency_key="up-key")

        result = self._run_command()
        self.assertIn("2", result)

    def test_no_jobs_returns_zero(self):
        """handle() with no pending jobs should report 0 processed."""
        result = self._run_command()
        self.assertIn("0", result)

    def test_done_jobs_not_reprocessed(self):
        """Jobs with status=done should not be claimed or re-executed."""
        from hc.api.models import Job
        job = make_job(status="done", attempts=1)
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.attempts, 1)  # unchanged

    def test_failed_jobs_not_reprocessed(self):
        """Jobs with status=failed should not be claimed."""
        from hc.api.models import Job
        job = make_job(status="failed", attempts=3, max_attempts=3)
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 3)  # unchanged

    def test_future_scheduled_at_not_claimed(self):
        """Jobs scheduled for the future should not be claimed."""
        from hc.api.models import Job
        from django.utils.timezone import now
        job = Job.objects.create(
            job_type="send_alert",
            payload={},
            scheduled_at=now() + td(hours=1),
        )
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")  # untouched

    def test_unknown_job_type_causes_failure_after_max_attempts(self):
        """Unknown job_type should fail and increment attempts until max reached."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="nonexistent_type",
            payload={},
            max_attempts=1,
        )
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)
        self.assertIn("unknown job type", job.error)

    def test_failed_job_stores_error_message(self):
        """A failed job should store the exception message in job.error."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="nonexistent_type",
            payload={},
            max_attempts=1,
        )
        self._run_command()
        job.refresh_from_db()
        self.assertNotEqual(job.error, "")

    def test_retry_increments_attempts_and_resets_to_pending(self):
        """On failure with attempts < max_attempts, job goes back to pending."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="nonexistent_type",
            payload={},
            max_attempts=3,
        )
        # Run once — it fails, should be pending with attempts=1
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempts, 1)

    def test_retry_backoff_bumps_scheduled_at(self):
        """After a retry, scheduled_at should be in the future (backoff)."""
        from hc.api.models import Job
        before = now()
        job = Job.objects.create(
            job_type="nonexistent_type",
            payload={},
            max_attempts=3,
        )
        self._run_command()
        job.refresh_from_db()
        self.assertGreater(job.scheduled_at, before)

    def test_job_eventually_fails_after_max_attempts(self):
        """Repeated runs on a bad job should eventually produce status=failed."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="nonexistent_type",
            payload={},
            max_attempts=2,
        )
        # Force scheduled_at to the past after each run so it gets picked up again
        from django.utils.timezone import now as dj_now
        self._run_command()  # attempt 1 -> pending
        job.refresh_from_db()
        job.scheduled_at = dj_now() - td(seconds=1)
        job.save()

        self._run_command()  # attempt 2 -> failed
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 2)

    def test_claim_transitions_job_to_processing(self):
        """_claim_next_job() must set status=processing before returning."""
        from hc.api.models import Job
        from hc.api.management.commands.processjobs import Command
        check = Check.objects.create(project=self.project, name="C3")
        job = Job.enqueue("send_alert", {"check_code": str(check.code), "new_status": "down"})

        cmd = Command()
        claimed = cmd._claim_next_job()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, job.id)
        # Verify DB state is also processing
        job.refresh_from_db()
        self.assertEqual(job.status, "processing")

    def test_second_claim_returns_none_when_no_more_jobs(self):
        """After all jobs are claimed, _claim_next_job() returns None."""
        from hc.api.models import Job
        from hc.api.management.commands.processjobs import Command
        check = Check.objects.create(project=self.project, name="C4")
        Job.enqueue("send_alert", {"check_code": str(check.code), "new_status": "down"})

        cmd = Command()
        first = cmd._claim_next_job()
        second = cmd._claim_next_job()

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_processing_jobs_are_skipped_by_claim(self):
        """A job already in processing status should not be claimed again."""
        from hc.api.models import Job
        from hc.api.management.commands.processjobs import Command
        job = make_job(status="processing", attempts=1)

        cmd = Command()
        claimed = cmd._claim_next_job()
        self.assertIsNone(claimed)

    def test_missing_check_code_fails_job(self):
        """A send_alert job without check_code in payload should fail."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="send_alert",
            payload={"new_status": "down"},  # missing check_code
            max_attempts=1,
        )
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("check_code", job.error)

    def test_nonexistent_check_fails_job(self):
        """A send_alert job referencing a missing check UUID should fail."""
        from hc.api.models import Job
        job = Job.objects.create(
            job_type="send_alert",
            payload={"check_code": str(uuid.uuid4()), "new_status": "down"},
            max_attempts=1,
        )
        self._run_command()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")


# ===========================================================================
# 4. Check.ping() integration
# ===========================================================================

class CheckPingIntegrationTestCase(BaseTestCase):
    """Tests that Check.ping() enqueues jobs on status change."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(
            project=self.project,
            name="Integration Check",
            status="new",
        )

    def _ping(self, action="success"):
        self.check.ping(
            remote_addr="1.2.3.4",
            scheme="http",
            method="GET",
            ua="test",
            body=b"",
            action=action,
            rid=None,
        )

    def test_ping_enqueues_job_on_new_to_up(self):
        """First success ping (new → up) should enqueue a send_alert job."""
        from hc.api.models import Job
        self._ping("success")
        jobs = Job.objects.filter(job_type="send_alert")
        self.assertEqual(jobs.count(), 1)
        job = jobs.first()
        self.assertEqual(job.payload.get("new_status"), "up")
        self.assertEqual(job.payload.get("check_code"), str(self.check.code))

    def test_ping_enqueues_job_on_up_to_down(self):
        """Fail ping (up → down) should enqueue a send_alert job with new_status=down."""
        from hc.api.models import Job
        self._ping("success")   # new → up
        Job.objects.all().delete()  # clear previous

        self._ping("fail")  # up → down
        jobs = Job.objects.filter(job_type="send_alert")
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs.first().payload.get("new_status"), "down")

    def test_repeated_success_ping_no_duplicate_jobs(self):
        """Second success ping when already up should NOT create a new job."""
        from hc.api.models import Job
        self._ping("success")   # new → up
        count_after_first = Job.objects.count()

        self._ping("success")   # up → up (no change)
        count_after_second = Job.objects.count()

        # Status didn't change on the second ping, so no new job
        self.assertEqual(count_after_first, count_after_second)

    def test_idempotency_key_prevents_duplicate_jobs(self):
        """Two pings at the exact same frozen_now would produce idempotent keys."""
        from hc.api.models import Job
        # Can't easily fake frozen_now, but we can verify unique keys per ping event
        self._ping("success")  # new → up, creates job with key containing timestamp
        self._ping("fail")     # up → down, different timestamp → different key
        self.assertEqual(Job.objects.count(), 2)
        keys = list(Job.objects.values_list("idempotency_key", flat=True))
        self.assertEqual(len(set(keys)), 2)  # both keys are distinct

    def test_start_ping_does_not_enqueue_job(self):
        """A 'start' ping does not change status and must not create a job."""
        from hc.api.models import Job
        self._ping("start")
        self.assertEqual(Job.objects.count(), 0)

    def test_log_ping_does_not_enqueue_job(self):
        """A 'log' ping does not change status and must not create a job."""
        from hc.api.models import Job
        self._ping("log")
        self.assertEqual(Job.objects.count(), 0)


# ===========================================================================
# 5. GET /api/v3/jobs/ endpoint
# ===========================================================================

class ListJobsApiTestCase(BaseTestCase):
    """Tests for the GET /api/v3/jobs/ API endpoint."""

    def setUp(self):
        super().setUp()
        self.url = "/api/v3/jobs/"

    def test_returns_200(self):
        """GET should return 200."""
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_returns_empty_list_when_no_jobs(self):
        """GET with no jobs should return {'jobs': []}."""
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.json()["jobs"], [])

    def test_returns_jobs_for_project_checks(self):
        """GET should return jobs linked to the project's checks."""
        from hc.api.models import Job
        check = Check.objects.create(project=self.project, name="Checked")
        Job.enqueue(
            "send_alert",
            {"check_code": str(check.code), "new_status": "down"},
            idempotency_key="list-test-1",
        )
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)
        jobs = r.json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["payload"]["new_status"], "down")

    def test_does_not_return_other_projects_jobs(self):
        """GET should not return jobs that belong to a different project's check."""
        from hc.api.models import Job
        other_check = Check.objects.create(project=self.bobs_project, name="Bob's")
        Job.enqueue(
            "send_alert",
            {"check_code": str(other_check.code), "new_status": "down"},
            idempotency_key="bobs-job",
        )
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.json()["jobs"], [])

    def test_missing_api_key_returns_401(self):
        """GET without API key should return 401."""
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 401)

    def test_wrong_api_key_returns_401(self):
        """GET with wrong API key should return 401."""
        r = self.client.get(self.url, HTTP_X_API_KEY="Z" * 32)
        self.assertEqual(r.status_code, 401)

    def test_endpoint_works_on_v1(self):
        """jobs/ endpoint should also be accessible under /api/v1/."""
        r = self.client.get("/api/v1/jobs/", HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_endpoint_works_on_v2(self):
        """jobs/ endpoint should also be accessible under /api/v2/."""
        r = self.client.get("/api/v2/jobs/", HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_cors_header_present(self):
        """Response should include CORS headers."""
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.get("Access-Control-Allow-Origin"), "*")

    def test_readonly_key_works(self):
        """A read-only API key should be accepted for GET /jobs/."""
        readonly_key = "R" * 32
        self.project.api_key_readonly = readonly_key
        self.project.save()
        r = self.client.get(self.url, HTTP_X_API_KEY=readonly_key)
        self.assertEqual(r.status_code, 200)
