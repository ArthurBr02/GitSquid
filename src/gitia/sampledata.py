from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .models import (
    Change,
    ChangeRepo,
    ChangeSource,
    ChangeStatus,
    EventLog,
    TestRun,
    TestRunRepo,
)

# Records of plausible past work on a fictional `billing` module. They are flagged
# is_sample=1 everywhere, shown as SAMPLE in the UI, and removed by `gitia sample clear`.

_RETRY_DIFF = """diff --git a/billing/invoices.py b/billing/invoices.py
--- a/billing/invoices.py
+++ b/billing/invoices.py
@@ -12,7 +12,11 @@ def fetch_invoice(client, invoice_id):
-    response = client.get(f"/invoices/{invoice_id}")
-    return Invoice.from_payload(response.json())
+    for attempt in range(3):
+        response = client.get(f"/invoices/{invoice_id}")
+        if response.status_code != 503:
+            break
+        time.sleep(2**attempt)
+    return Invoice.from_payload(response.json())
"""

_ROUNDING_DIFF = """diff --git a/billing/totals.py b/billing/totals.py
--- a/billing/totals.py
+++ b/billing/totals.py
@@ -4,6 +4,6 @@ from decimal import Decimal, ROUND_HALF_UP
 def line_total(unit_price, quantity):
-    return round(unit_price * quantity, 2)
+    return (Decimal(unit_price) * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
"""

_TIMEZONE_DIFF = """diff --git a/billing/periods.py b/billing/periods.py
--- a/billing/periods.py
+++ b/billing/periods.py
@@ -1,5 +1,5 @@
-from datetime import datetime
+from datetime import datetime, timezone
 
 def period_start(now=None):
-    now = now or datetime.now()
+    now = now or datetime.now(timezone.utc)
     return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
"""


@dataclass(frozen=True)
class SampleRecord:
    task: str
    rationale: str
    diff: str
    files: list[str]
    status: ChangeStatus
    exit_code: int
    output: str


SAMPLES: tuple[SampleRecord, ...] = (
    SampleRecord(
        task="Retry invoice fetches when the billing API answers 503",
        rationale="The upstream API returns 503 during its nightly window. Three attempts with "
        "exponential backoff cover it without masking real failures.",
        diff=_RETRY_DIFF,
        files=["billing/invoices.py"],
        status=ChangeStatus.VERIFIED,
        exit_code=0,
        output="collected 48 items\n48 passed in 3.71s",
    ),
    SampleRecord(
        task="Use Decimal for line totals so cents stop drifting",
        rationale="Float rounding lost a cent on quantities above 7. Decimal with ROUND_HALF_UP "
        "matches what the finance team reconciles against.",
        diff=_ROUNDING_DIFF,
        files=["billing/totals.py"],
        status=ChangeStatus.VERIFIED,
        exit_code=0,
        output="collected 48 items\n48 passed in 3.55s",
    ),
    SampleRecord(
        task="Make billing periods timezone-aware",
        rationale="period_start used a naive local datetime, so invoices generated after 23:00 "
        "landed in the previous period.",
        diff=_TIMEZONE_DIFF,
        files=["billing/periods.py"],
        status=ChangeStatus.REVERTED,
        exit_code=1,
        output="FAILED tests/test_periods.py::test_period_start_matches_ledger\n"
        "1 failed, 47 passed in 3.62s",
    ),
)


def load(conn: sqlite3.Connection) -> int:
    changes = ChangeRepo(conn)
    runs = TestRunRepo(conn)
    events = EventLog(conn)
    created = 0
    for sample in SAMPLES:
        change = Change(
            task=sample.task,
            diff=sample.diff,
            source=ChangeSource.SAMPLE,
            rationale=sample.rationale,
            files_touched=sample.files,
            is_sample=True,
        )
        changes.add(change)
        change.status = sample.status
        change.applied_at = change.created_at
        changes.save(change)
        runs.add(
            TestRun(
                change_id=change.id,
                command="pytest -q",
                exit_code=sample.exit_code,
                duration_ms=3700,
                output_tail=sample.output,
                is_sample=True,
            )
        )
        events.record("proposed", "sample record", change_id=change.id, is_sample=True)
        events.record(
            "tested",
            f"`pytest -q` exited {sample.exit_code}",
            change_id=change.id,
            is_sample=True,
        )
        created += 1
    return created


def clear(conn: sqlite3.Connection) -> dict[str, int]:
    events = EventLog(conn).delete_samples()
    runs = TestRunRepo(conn).delete_samples()
    changes = ChangeRepo(conn).delete_samples()
    return {"changes": changes, "test_runs": runs, "events": events}


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM changes WHERE is_sample=1").fetchone()[0])
