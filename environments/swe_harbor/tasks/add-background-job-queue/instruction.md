# Add Background Job Queue

The Healthchecks codebase is at `/app/`. It's a Django app for monitoring cron jobs.

## What to build

Introduce a database-backed background job queue to Healthchecks. Jobs are created by
application logic (such as `Check.ping()` when a status change occurs) and processed
safely and exactly once by a management command. The queue must guarantee no duplicate
execution under concurrent workers, support retry with exponential back-off, and
enforce idempotency via unique keys.

---

## 1. `Job` model (`/app/hc/api/models.py`)

Add after the `Flip` model (near the bottom of the file):

```python
class Job(models.Model):
    JOB_STATUSES = (
        ("pending",    "Pending"),
        ("processing", "Processing"),
        ("done",       "Done"),
        ("failed",     "Failed"),
    )

    code             = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    job_type         = models.CharField(max_length=100)
    payload          = models.JSONField(default=dict)
    status           = models.CharField(max_length=20, choices=JOB_STATUSES, default="pending")
    attempts         = models.IntegerField(default=0)
    max_attempts     = models.IntegerField(default=3)
    idempotency_key  = models.CharField(max_length=200, blank=True, db_index=True)
    created          = models.DateTimeField(default=now)
    scheduled_at     = models.DateTimeField(default=now)
    started_at       = models.DateTimeField(null=True, blank=True)
    completed_at     = models.DateTimeField(null=True, blank=True)
    error            = models.TextField(blank=True)

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
            "uuid":             str(self.code),
            "job_type":         self.job_type,
            "payload":          self.payload,
            "status":           self.status,
            "attempts":         self.attempts,
            "max_attempts":     self.max_attempts,
            "idempotency_key":  self.idempotency_key,
            "created":          isostring(self.created),
            "scheduled_at":     isostring(self.scheduled_at),
            "started_at":       isostring(self.started_at),
            "completed_at":     isostring(self.completed_at),
            "error":            self.error,
        }
```

---

## 2. Migration (`/app/hc/api/migrations/`)

Generate with:

```bash
python manage.py makemigrations api --name job
```

---

## 3. `Check.enqueue_alert_job()` (`/app/hc/api/models.py`)

Add this method to the `Check` class, after the `prune()` method:

```python
def enqueue_alert_job(self, new_status: str, frozen_now) -> None:
    """Enqueue a background job to handle a check status change."""
    Job.enqueue(
        job_type="send_alert",
        payload={"check_code": str(self.code), "new_status": new_status},
        idempotency_key=f"alert-{self.code}-{new_status}-{frozen_now.isoformat()}",
    )
```

---

## 4. Modify `Check.ping()` (`/app/hc/api/models.py`)

Inside the `Check.ping()` method, locate the block that detects a status change and
creates a flip. It reads:

```python
                new_status = "down" if action == "fail" else "up"
                if self.status != new_status:
                    self.create_flip(new_status)
                    self.status = new_status
```

Extend it to also enqueue a job when status changes:

```python
                new_status = "down" if action == "fail" else "up"
                if self.status != new_status:
                    self.create_flip(new_status)
                    self.status = new_status
                    self.enqueue_alert_job(new_status, frozen_now)
```

`frozen_now` is already defined earlier in the same `transaction.atomic()` block.

---

## 5. Management command (`/app/hc/api/management/commands/processjobs.py`)

Create this new file. The command must:

1. **Atomically claim** the next eligible `pending` job whose `scheduled_at <= now()`,
   using `select_for_update(skip_locked=True)` inside `transaction.atomic()`.  
   In the same atomic block, set `status="processing"`, increment `attempts`, set
   `started_at=now()`, and save.
2. **Execute** the job by calling `execute_job(job)` (defined below).
3. **On success**: set `status="done"`, `completed_at=now()`, save.
4. **On failure** (any exception from `execute_job`):
   - Store `str(e)` in `job.error`.
   - If `job.attempts >= job.max_attempts`: set `status="failed"`.
   - Otherwise: set `status="pending"` and set `scheduled_at = now() + timedelta(seconds=60 * job.attempts)` (exponential back-off).
   - Save.
5. Repeat until no more eligible jobs remain.
6. Return `"Processed N job(s)."` as a string.

**`execute_job(job)`** function (module-level, not a method):

```python
def execute_job(job):
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
        # This job records intent and confirms the check exists.
    else:
        raise ValueError(f"unknown job type: {job.job_type}")
```

---

## 6. `GET /api/v3/jobs/` endpoint (`/app/hc/api/views.py` and `/app/hc/api/urls.py`)

### View

Add in `hc/api/views.py`:

```python
@cors("GET")
@csrf_exempt
@authorize_read
def list_jobs(request: ApiRequest) -> JsonResponse:
    """List all jobs for the authenticated project's checks."""
    from hc.api.models import Job
    check_codes = request.project.check_set.values_list("code", flat=True)
    jobs = Job.objects.filter(
        payload__check_code__in=[str(c) for c in check_codes]
    ).order_by("scheduled_at", "created")
    return JsonResponse({"jobs": [j.to_dict() for j in jobs]})
```

### URL route

Add to the `api_urls` list in `hc/api/urls.py`:

```python
path("jobs/", views.list_jobs, name="hc-api-jobs"),
```

---

## Constraints

- `select_for_update(skip_locked=True)` **must** be used inside `transaction.atomic()` when claiming jobs — this is the core concurrency guarantee.
- `Job.enqueue()` uses `get_or_create` on `idempotency_key` — calling it twice with the same key must not create two rows.
- The `processing` status is a transient state: a job that crashes mid-execution does **not** stay `processing` — the command sets it back to `pending` (with back-off) or `failed`.
- Retry back-off formula: `scheduled_at = now() + timedelta(seconds=60 * attempts)` after the failed attempt.
- `done` and `failed` jobs are never re-claimed.
- Don't modify existing tests.
- Follow existing patterns for decorators (`@authorize_read`, `@cors`, `@csrf_exempt`).
