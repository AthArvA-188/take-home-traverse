#!/bin/bash
set -e
cd /app

###############################################################################
# 1. Append Job model to hc/api/models.py
###############################################################################

cat >> /app/hc/api/models.py << 'PYEOF'


class Job(models.Model):
    JOB_STATUSES = (
        ("pending",    "Pending"),
        ("processing", "Processing"),
        ("done",       "Done"),
        ("failed",     "Failed"),
    )

    code            = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    job_type        = models.CharField(max_length=100)
    payload         = models.JSONField(default=dict)
    status          = models.CharField(max_length=20, choices=JOB_STATUSES, default="pending")
    attempts        = models.IntegerField(default=0)
    max_attempts    = models.IntegerField(default=3)
    idempotency_key = models.CharField(max_length=200, blank=True, db_index=True)
    created         = models.DateTimeField(default=now)
    scheduled_at    = models.DateTimeField(default=now)
    started_at      = models.DateTimeField(null=True, blank=True)
    completed_at    = models.DateTimeField(null=True, blank=True)
    error           = models.TextField(blank=True)

    class Meta:
        ordering = ["scheduled_at", "created"]

    @classmethod
    def enqueue(cls, job_type: str, payload: dict, idempotency_key: str = "") -> "Job":
        """Create a pending job. If idempotency_key is given and a job with that
        key already exists, return the existing job without creating a duplicate."""
        if idempotency_key:
            job, _ = cls.objects.get_or_create(
                idempotency_key=idempotency_key,
                defaults={
                    "job_type": job_type,
                    "payload": payload,
                    "status": "pending",
                },
            )
            return job
        return cls.objects.create(job_type=job_type, payload=payload)

    def to_dict(self) -> dict:
        return {
            "uuid":            str(self.code),
            "job_type":        self.job_type,
            "payload":         self.payload,
            "status":          self.status,
            "attempts":        self.attempts,
            "max_attempts":    self.max_attempts,
            "idempotency_key": self.idempotency_key,
            "created":         isostring(self.created),
            "scheduled_at":    isostring(self.scheduled_at),
            "started_at":      isostring(self.started_at),
            "completed_at":    isostring(self.completed_at),
            "error":           self.error,
        }
PYEOF

###############################################################################
# 2. Add Check.enqueue_alert_job() method after prune()
###############################################################################

python3 << 'PATCH1'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''        except Ping.DoesNotExist:
            pass

    @property
    def visible_pings(self) -> QuerySet["Ping"]:'''

new = '''        except Ping.DoesNotExist:
            pass

    def enqueue_alert_job(self, new_status: str, frozen_now) -> None:
        """Enqueue a background job to handle a check status change."""
        Job.enqueue(
            job_type="send_alert",
            payload={"check_code": str(self.code), "new_status": new_status},
            idempotency_key=f"alert-{self.code}-{new_status}-{frozen_now.isoformat()}",
        )

    @property
    def visible_pings(self) -> QuerySet["Ping"]:'''

assert old in content, "PATCH1: anchor string not found"
content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH1

###############################################################################
# 3. Modify Check.ping() to enqueue a job on status change
###############################################################################

python3 << 'PATCH2'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''                new_status = "down" if action == "fail" else "up"
                if self.status != new_status:
                    self.create_flip(new_status)
                    self.status = new_status'''

new = '''                new_status = "down" if action == "fail" else "up"
                if self.status != new_status:
                    self.create_flip(new_status)
                    self.status = new_status
                    self.enqueue_alert_job(new_status, frozen_now)'''

assert old in content, "PATCH2: anchor string not found"
content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH2

###############################################################################
# 4. Create the processjobs management command
###############################################################################

cat > /app/hc/api/management/commands/processjobs.py << 'PYEOF'
from __future__ import annotations

from datetime import timedelta as td
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.timezone import now

from hc.api.models import Check, Job


def execute_job(job: Job) -> None:
    """Execute a single job based on its type."""
    if job.job_type == "send_alert":
        check_code = job.payload.get("check_code")
        new_status = job.payload.get("new_status")
        if not check_code:
            raise ValueError("missing check_code in payload")
        try:
            Check.objects.get(code=check_code)
        except Check.DoesNotExist:
            raise ValueError(f"check {check_code} not found")
        # Real alert dispatch is handled by sendalerts via Flips.
        # This job confirms the check exists and records intent.
    else:
        raise ValueError(f"unknown job type: {job.job_type}")


class Command(BaseCommand):
    help = "Process pending background jobs exactly once."

    def handle(self, **options: Any) -> str:
        processed = 0
        while True:
            job = self._claim_next_job()
            if job is None:
                break
            self._execute(job)
            processed += 1
        return f"Processed {processed} job(s)."

    def _claim_next_job(self) -> Job | None:
        """Atomically claim the next eligible pending job.

        Uses select_for_update(skip_locked=True) so concurrent workers never
        claim the same job.
        """
        with transaction.atomic():
            job = (
                Job.objects.select_for_update(skip_locked=True)
                .filter(status="pending", scheduled_at__lte=now())
                .first()
            )
            if job is None:
                return None

            job.status = "processing"
            job.started_at = now()
            job.attempts += 1
            job.save()
            return job

    def _execute(self, job: Job) -> None:
        """Execute a claimed job and update its status."""
        try:
            execute_job(job)
            with transaction.atomic():
                job.status = "done"
                job.completed_at = now()
                job.save()
        except Exception as e:
            with transaction.atomic():
                job.error = str(e)
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                else:
                    job.status = "pending"
                    delay_secs = 60 * job.attempts
                    job.scheduled_at = now() + td(seconds=delay_secs)
                job.save()
PYEOF

###############################################################################
# 5. Add list_jobs view to hc/api/views.py
###############################################################################

cat >> /app/hc/api/views.py << 'VIEWEOF'


@cors("GET")
@csrf_exempt
@authorize_read
def list_jobs(request: ApiRequest) -> JsonResponse:
    """List all jobs for the authenticated project's checks."""
    from hc.api.models import Job
    check_codes = list(request.project.check_set.values_list("code", flat=True))
    jobs = Job.objects.filter(
        payload__check_code__in=[str(c) for c in check_codes]
    ).order_by("scheduled_at", "created")
    return JsonResponse({"jobs": [j.to_dict() for j in jobs]})
VIEWEOF

###############################################################################
# 6. Add URL route to hc/api/urls.py
###############################################################################

python3 << 'PATCH3'
with open("hc/api/urls.py", "r") as f:
    content = f.read()

old = '    path("channels/", views.channels),'

new = '''    path("jobs/", views.list_jobs, name="hc-api-jobs"),
    path("channels/", views.channels),'''

assert old in content, "PATCH3: anchor string not found"
content = content.replace(old, new, 1)

with open("hc/api/urls.py", "w") as f:
    f.write(content)
PATCH3

###############################################################################
# 7. Generate migration and apply
###############################################################################

python manage.py makemigrations api --name job 2>&1
python manage.py migrate 2>&1
