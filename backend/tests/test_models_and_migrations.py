from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    INFERENCE_JOB_STATUSES,
    InferenceJob,
    InferenceJobStatus,
    InputMedia,
)
from app.schemas import InferenceJobAccepted, InferenceJobDTO

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def _load_alembic_runtime():
    spec = importlib.util.spec_from_file_location(
        "infrascan_alembic_runtime",
        BACKEND_DIR / "alembic" / "runtime.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_pg_bin_dir() -> Path | None:
    # Prefer PostgreSQL 16 to match `postgres:16-alpine` in `compose.yaml`
    # and avoid PG 18 `io_uring` syscalls that may be blocked in sandboxes.
    pg16_bin = Path("/usr/lib/postgresql/16/bin")
    if (pg16_bin / "initdb").is_file() and (pg16_bin / "pg_ctl").is_file():
        return pg16_bin

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if initdb and pg_ctl:
        return Path(initdb).parent

    pg_root = Path("/usr/lib/postgresql")
    if pg_root.is_dir():
        for version_dir in sorted(pg_root.iterdir()):
            bin_dir = version_dir / "bin"
            if (bin_dir / "initdb").is_file() and (bin_dir / "pg_ctl").is_file():
                return bin_dir
    return None


def _run_alembic(
    *args: str,
    cwd: Path = BACKEND_DIR,
    database_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if database_url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url

    alembic_exe = Path(os.sys.executable).with_name("alembic")
    return subprocess.run(
        [str(alembic_exe), *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_shared_metadata_defines_only_application_tables() -> None:
    assert set(Base.metadata.tables) == {"input_media", "inference_jobs"}
    assert "users" not in Base.metadata.tables
    assert "celery_taskmeta" not in Base.metadata.tables
    assert "celery_tasksetmeta" not in Base.metadata.tables


def test_input_media_model_columns_and_relationship() -> None:
    table = InputMedia.__table__
    assert set(table.columns.keys()) == {
        "media_id",
        "data",
        "content_type",
        "filename",
    }

    assert table.c.media_id.primary_key is True
    assert table.c.media_id.nullable is False
    assert table.c.data.nullable is False
    assert (
        table.c.data.type.dialect_impl(postgresql.dialect()).__class__
        is postgresql.BYTEA
    )
    assert table.c.content_type.nullable is False
    assert table.c.filename.nullable is True

    rel = sa.inspect(InputMedia).relationships["jobs"]
    assert rel.back_populates == "input_media"
    assert rel.passive_deletes == "all"


def test_inference_job_model_columns_defaults_and_constraints() -> None:
    assert INFERENCE_JOB_STATUSES == ("pending", "running", "completed", "failed")
    assert InferenceJobStatus.PENDING == "pending"

    table = InferenceJob.__table__
    assert set(table.columns.keys()) == {
        "job_id",
        "user_id",
        "input_media_id",
        "status",
        "model_version",
        "created_at",
        "started_at",
        "completed_at",
        "captured_at",
        "latitude",
        "longitude",
    }

    assert table.c.job_id.primary_key is True
    assert table.c.job_id.nullable is False
    assert table.c.job_id.default is not None

    # Authentication is not implemented and no users table exists yet.
    assert table.c.user_id.nullable is True
    assert len(table.c.user_id.foreign_keys) == 0

    # Required FK to input_media with ON DELETE RESTRICT, non-unique index.
    assert table.c.input_media_id.nullable is False
    assert not table.c.input_media_id.unique
    fk = next(iter(table.c.input_media_id.foreign_keys))
    assert fk.target_fullname == "input_media.media_id"
    assert fk.ondelete == "RESTRICT"
    media_indexes = [
        idx
        for idx in table.indexes
        if [col.name for col in idx.columns] == ["input_media_id"]
    ]
    assert len(media_indexes) == 1
    assert media_indexes[0].unique is False

    # Status defaults to pending; model_version has no invented default.
    assert table.c.status.nullable is False
    assert table.c.status.default.arg == "pending"
    assert table.c.status.server_default is not None
    assert table.c.model_version.nullable is True
    assert table.c.model_version.default is None
    assert table.c.model_version.server_default is None

    # Timezone-aware timestamps.
    for ts_col_name in ("created_at", "started_at", "completed_at", "captured_at"):
        col = table.c[ts_col_name]
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True

    assert table.c.created_at.nullable is False
    assert table.c.created_at.server_default is not None
    assert table.c.started_at.nullable is True
    assert table.c.started_at.default is None
    assert table.c.started_at.server_default is None
    assert table.c.completed_at.nullable is True
    assert table.c.completed_at.default is None
    assert table.c.completed_at.server_default is None
    assert table.c.captured_at.nullable is False
    assert table.c.captured_at.default is None
    assert table.c.captured_at.server_default is None

    # Numeric coordinates.
    assert isinstance(table.c.latitude.type, sa.Numeric)
    assert table.c.latitude.nullable is False
    assert isinstance(table.c.longitude.type, sa.Numeric)
    assert table.c.longitude.nullable is False

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert check_names == {
        "ck_inference_jobs_status_allowed",
        "ck_inference_jobs_latitude_range",
        "ck_inference_jobs_longitude_range",
    }

    rel = sa.inspect(InferenceJob).relationships["input_media"]
    assert rel.back_populates == "jobs"


def test_preserves_submitted_at_api_contract_and_avoids_startup_create_all() -> None:
    assert "submitted_at" in InferenceJobAccepted.model_fields
    assert "created_at" not in InferenceJobAccepted.model_fields
    assert "submitted_at" in InferenceJobDTO.model_fields
    assert "created_at" not in InferenceJobDTO.model_fields

    main_source = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
    assert "create_all" not in main_source


def test_alembic_config_never_hardcodes_credentials_and_normalizes_driver() -> None:
    ini_text = ALEMBIC_INI.read_text(encoding="utf-8")
    for line in ini_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not stripped.startswith("sqlalchemy.url")
        assert "infrascan_dev" not in stripped

    runtime = _load_alembic_runtime()
    resolve_database_url = runtime.resolve_database_url
    include_name = runtime.include_name

    assert (
        resolve_database_url("postgresql://user:p%40ss@localhost:5432/infrascan")
        == "postgresql+psycopg://user:p%40ss@localhost:5432/infrascan"
    )
    assert (
        resolve_database_url("postgres://user:secret@localhost:5432/infrascan")
        == "postgresql+psycopg://user:secret@localhost:5432/infrascan"
    )
    assert (
        resolve_database_url(
            "postgresql+psycopg://user:secret@localhost:5432/infrascan"
        )
        == "postgresql+psycopg://user:secret@localhost:5432/infrascan"
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        resolve_database_url("")

    secret = "top-secret-password-value"
    with pytest.raises(RuntimeError) as exc_info:
        resolve_database_url(f"mysql://user:{secret}@localhost:3306/db")
    assert secret not in str(exc_info.value)

    with pytest.raises(RuntimeError) as bad_url_exc:
        resolve_database_url(f"://broken-{secret}")
    assert secret not in str(bad_url_exc.value)

    assert include_name("celery_taskmeta", "table", {}) is False
    assert include_name("celery_tasksetmeta", "table", {}) is False
    assert include_name("inference_jobs", "table", {}) is True
    assert include_name("input_media", "table", {}) is True


def test_alembic_renders_upgrade_and_downgrade_sql_offline() -> None:
    upgrade = _run_alembic(
        "-c",
        str(ALEMBIC_INI),
        "upgrade",
        "head",
        "--sql",
        cwd=REPO_ROOT,
        database_url="postgresql://localhost/infrascan",
    )
    assert upgrade.returncode == 0, upgrade.stderr
    sql = upgrade.stdout

    media_pos = sql.index("CREATE TABLE input_media")
    jobs_pos = sql.index("CREATE TABLE inference_jobs")
    assert media_pos < jobs_pos
    assert "data BYTEA NOT NULL" in sql
    assert "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ck_inference_jobs_status_allowed" in sql
    assert "ck_inference_jobs_latitude_range" in sql
    assert "ck_inference_jobs_longitude_range" in sql

    downgrade = _run_alembic(
        "downgrade",
        "head:base",
        "--sql",
        cwd=BACKEND_DIR,
        database_url="postgresql://localhost/infrascan",
    )
    assert downgrade.returncode == 0, downgrade.stderr
    down_sql = downgrade.stdout
    assert down_sql.index("DROP TABLE inference_jobs") < down_sql.index(
        "DROP TABLE input_media"
    )
    assert "celery_taskmeta" not in down_sql


def test_alembic_online_requires_database_url() -> None:
    result = _run_alembic("current", cwd=BACKEND_DIR, database_url=None)
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


@pytest.fixture
def disposable_postgres_url() -> str:
    pg_bin = _find_pg_bin_dir()
    if pg_bin is None:
        pytest.skip("Local PostgreSQL server binaries (initdb/pg_ctl) not installed")

    # Use a short path for Unix domain socket compatibility (< 107 bytes on Linux).
    base_tmp = Path("/tmp") if os.access("/tmp", os.W_OK) else BACKEND_DIR
    cluster_root = Path(tempfile.mkdtemp(prefix=".tmp_ispg_", dir=base_tmp))
    pgdata = cluster_root / "data"
    socket_dir = cluster_root / "sock"
    logfile = cluster_root / "postgres.log"
    socket_dir.mkdir(mode=0o700)

    started = False
    try:
        try:
            init_res = subprocess.run(
                [
                    str(pg_bin / "initdb"),
                    "-D",
                    str(pgdata),
                    "-U",
                    "postgres",
                    "-A",
                    "trust",
                    "--no-locale",
                    "-E",
                    "UTF8",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("Disposable PostgreSQL initdb timed out in restricted environment")

        if init_res.returncode != 0:
            pytest.skip(
                f"Disposable PostgreSQL initdb unavailable: {init_res.stderr.strip()}"
            )

        pg_opts = (
            f"-F -c listen_addresses='' "
            f"-c dynamic_shared_memory_type=mmap -k {socket_dir}"
        )
        try:
            start_res = subprocess.run(
                [
                    str(pg_bin / "pg_ctl"),
                    "-D",
                    str(pgdata),
                    "-l",
                    str(logfile),
                    "-o",
                    pg_opts,
                    "-t",
                    "8",
                    "-w",
                    "start",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("Disposable PostgreSQL start timed out in restricted environment")

        if start_res.returncode != 0:
            log_tail = logfile.read_text(encoding="utf-8") if logfile.exists() else ""
            pytest.skip(f"Disposable PostgreSQL start unavailable: {log_tail}")
        started = True

        subprocess.run(
            [
                str(pg_bin / "createdb"),
                "-h",
                str(socket_dir),
                "-U",
                "postgres",
                "infrascan_test",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Supply standard `postgresql://` scheme so Alembic exercises driver normalization.
        yield f"postgresql://postgres@/infrascan_test?host={socket_dir}"
    finally:
        if started:
            subprocess.run(
                [
                    str(pg_bin / "pg_ctl"),
                    "-D",
                    str(pgdata),
                    "-m",
                    "immediate",
                    "-t",
                    "5",
                    "-w",
                    "stop",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        shutil.rmtree(cluster_root, ignore_errors=True)


def test_disposable_postgres_upgrade_constraints_orm_and_downgrade(
    disposable_postgres_url: str,
) -> None:
    runtime = _load_alembic_runtime()
    engine = sa.create_engine(runtime.resolve_database_url(disposable_postgres_url))

    try:
        # Simulate pre-existing Celery result-backend tables in the same database.
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE celery_taskmeta (id SERIAL PRIMARY KEY, task_id VARCHAR(155) UNIQUE)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TABLE celery_tasksetmeta (id SERIAL PRIMARY KEY, taskset_id VARCHAR(155) UNIQUE)"
                )
            )

        up = _run_alembic("upgrade", "head", database_url=disposable_postgres_url)
        assert up.returncode == 0, up.stderr

        cur = _run_alembic("current", database_url=disposable_postgres_url)
        assert cur.returncode == 0, cur.stderr
        assert "7c4e9a1b2d01 (head)" in cur.stdout

        # Verify zero drift between ORM metadata and migration, ignoring Celery tables.
        chk = _run_alembic("check", database_url=disposable_postgres_url)
        assert chk.returncode == 0, f"{chk.stdout}\n{chk.stderr}"

        # Exercise ORM persistence, relationships, defaults, multiple jobs per media,
        # ON DELETE RESTRICT, and database CHECK constraints.
        captured = datetime(2026, 11, 10, 22, 30, 0, tzinfo=UTC)
        with Session(engine) as session:
            media = InputMedia(
                data=b"\xff\xd8\xff\xe0jpeg-bytes\xff\xd9",
                content_type="image/jpeg",
                filename="road.jpg",
            )
            session.add(media)
            session.flush()
            assert isinstance(media.media_id, UUID)

            job_one = InferenceJob(
                input_media=media,
                captured_at=captured,
                latitude=Decimal("33.88234567"),
                longitude=Decimal("-117.88512345"),
            )
            job_two = InferenceJob(
                user_id=uuid4(),
                input_media_id=media.media_id,
                status=InferenceJobStatus.RUNNING,
                model_version="yolo11n-v1",
                captured_at=captured,
                latitude=Decimal("-90"),
                longitude=Decimal("180"),
            )
            session.add_all([job_one, job_two])
            session.commit()

            session.refresh(job_one)
            session.refresh(job_two)
            session.refresh(media)

            assert isinstance(job_one.job_id, UUID)
            assert job_one.user_id is None
            assert job_one.status == "pending"
            assert job_one.model_version is None
            assert job_one.created_at.tzinfo is not None
            assert job_one.started_at is None
            assert job_one.completed_at is None
            assert job_one.captured_at == captured
            assert job_one.latitude == Decimal("33.88234567")
            assert job_one.longitude == Decimal("-117.88512345")
            assert len(media.jobs) == 2

            # Deleting media while referenced by jobs must be rejected.
            session.delete(media)
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()

            media_id = media.media_id

        # Verify invalid status and out-of-bounds / NaN coordinates are rejected by PostgreSQL.
        for bad_kwargs in (
            {
                "status": "queued",
                "latitude": Decimal("0"),
                "longitude": Decimal("0"),
            },
            {
                "status": "pending",
                "latitude": Decimal("90.0001"),
                "longitude": Decimal("0"),
            },
            {
                "status": "pending",
                "latitude": Decimal("-90.0001"),
                "longitude": Decimal("0"),
            },
            {
                "status": "pending",
                "latitude": Decimal("0"),
                "longitude": Decimal("180.0001"),
            },
            {
                "status": "pending",
                "latitude": Decimal("0"),
                "longitude": Decimal("-180.0001"),
            },
        ):
            with Session(engine) as session:
                session.add(
                    InferenceJob(
                        input_media_id=media_id,
                        captured_at=captured,
                        **bad_kwargs,
                    )
                )
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO inference_jobs (input_media_id, captured_at, latitude, longitude)
                        VALUES (:media_id, :captured_at, 'NaN'::numeric, 0)
                        """
                    ),
                    {"media_id": media_id, "captured_at": captured},
                )

        # Once referencing jobs are deleted, media can be deleted cleanly.
        with Session(engine) as session:
            for job in session.scalars(sa.select(InferenceJob)).all():
                session.delete(job)
            session.flush()
            stored_media = session.get(InputMedia, media_id)
            assert stored_media is not None
            session.delete(stored_media)
            session.commit()

        # Downgrade to base must remove only input_media and inference_jobs,
        # preserving pre-existing Celery result-backend tables.
        down = _run_alembic("downgrade", "base", database_url=disposable_postgres_url)
        assert down.returncode == 0, down.stderr

        inspector = sa.inspect(engine)
        remaining_tables = set(inspector.get_table_names())
        assert "inference_jobs" not in remaining_tables
        assert "input_media" not in remaining_tables
        assert {"celery_taskmeta", "celery_tasksetmeta"}.issubset(remaining_tables)
    finally:
        engine.dispose()
