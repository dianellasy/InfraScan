"""Link historical detections that have no Issue, with the worker's matching rule.

Run from backend/ with DATABASE_URL set, after `alembic upgrade head`:

    python -m app.maintenance.backfill_issue_links           # dry run, rolls back
    python -m app.maintenance.backfill_issue_links --apply   # commits

Only rows with `issue_id IS NULL` are considered, so a second run links
nothing new. Jobs are replayed in capture order through `IssueLinker`, holding
the same advisory lock as the worker. Unlocated detections are linked to
unlocated Issues for the review queue. Rows that cannot be linked are reported
and left unchanged:

- `unmapped_class`: the raw model class has no canonical Issue type.
- `unknown_observation_time`: a video detection without `observed_at`. Its
  frame time comes from the clip's GPS track, which PostgreSQL does not store,
  and guessing it would corrupt the Issue's first/last-seen times.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import create_db_engine, create_session_factory
from app.models import MODEL_CLASS_ISSUE_TYPES, DetectionRecord, EvidenceMedia, InferenceJob
from app.services.issue_linking import FrameDetections, IssueLinker, IssueMatchPolicy

UNMAPPED_CLASS = "unmapped_class"
UNKNOWN_OBSERVATION_TIME = "unknown_observation_time"


@dataclass
class BackfillReport:
    jobs: int = 0
    linked: int = 0
    created_issue_ids: set[UUID] = field(default_factory=set)
    updated_issue_ids: set[UUID] = field(default_factory=set)
    skipped: dict[str, list[UUID]] = field(default_factory=lambda: defaultdict(list))

    def as_dict(self) -> dict[str, object]:
        return {
            "jobs": self.jobs,
            "linked_detections": self.linked,
            "created_issues": len(self.created_issue_ids),
            "updated_existing_issues": len(self.updated_issue_ids),
            "skipped": {
                reason: [str(detection_id) for detection_id in ids]
                for reason, ids in sorted(self.skipped.items())
            },
        }


def _job_frames(
    session: Session, job: InferenceJob, records: list[DetectionRecord], report: BackfillReport
) -> list[FrameDetections]:
    by_evidence: dict[UUID | None, list[DetectionRecord]] = defaultdict(list)
    for record in records:
        if record.issue_type not in MODEL_CLASS_ISSUE_TYPES:
            report.skipped[UNMAPPED_CLASS].append(record.detection_id)
            continue
        if record.observed_at is None:
            if record.frame_timestamp is not None:
                report.skipped[UNKNOWN_OBSERVATION_TIME].append(record.detection_id)
                continue
            record.observed_at = job.captured_at
        by_evidence[record.evidence_media].append(record)

    # Every evidence row is a frame, including frames with nothing to link, so
    # unlocated tracks only continue across truly adjacent sampled frames.
    evidence_rows = session.scalars(
        select(EvidenceMedia)
        .where(EvidenceMedia.job_id == job.job_id)
        .order_by(EvidenceMedia.frame_timestamp.asc().nulls_first(), EvidenceMedia.media_id)
    ).all()
    frame_keys: list[UUID | None] = [None] if None in by_evidence else []
    frame_keys += [row.media_id for row in evidence_rows]
    frames = []
    for key in frame_keys:
        frame_records = by_evidence.get(key, [])
        observed_at = frame_records[0].observed_at if frame_records else job.captured_at
        frames.append(FrameDetections(job.job_id, key, observed_at, frame_records))
    return frames


def backfill_issue_links(session: Session, policy: IssueMatchPolicy) -> BackfillReport:
    """Link unlinked detections inside the caller's transaction."""
    linker = IssueLinker(session, policy)
    linker.lock()
    rows = session.execute(
        select(DetectionRecord, InferenceJob)
        .join(InferenceJob, InferenceJob.job_id == DetectionRecord.job_id)
        .where(DetectionRecord.issue_id.is_(None))
        .order_by(InferenceJob.captured_at, InferenceJob.created_at, InferenceJob.job_id)
    ).all()
    jobs: dict[UUID, tuple[InferenceJob, list[DetectionRecord]]] = {}
    for record, job in rows:
        jobs.setdefault(job.job_id, (job, []))[1].append(record)

    report = BackfillReport(jobs=len(jobs))
    frames = [
        frame
        for job, records in jobs.values()
        for frame in _job_frames(session, job, records, report)
    ]
    summary = linker.link(frames)
    report.linked = summary.linked
    report.created_issue_ids = summary.created_issue_ids
    report.updated_issue_ids = summary.updated_issue_ids
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply", action="store_true", help="commit the links (default: dry run)"
    )
    args = parser.parse_args(argv)
    engine = create_db_engine()
    try:
        with create_session_factory(engine)() as session:
            report = backfill_issue_links(session, IssueMatchPolicy.from_env())
            if args.apply:
                session.commit()
            else:
                session.rollback()
    finally:
        engine.dispose()
    print(json.dumps({"applied": args.apply, **report.as_dict()}, indent=2))


if __name__ == "__main__":
    main()
