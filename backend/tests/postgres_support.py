"""Disposable PostgreSQL clusters and Alembic subprocess helpers for tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]


def find_pg_bin_dir() -> Path | None:
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


def run_alembic(
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

    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@contextmanager
def disposable_postgres() -> Iterator[str]:
    """Start a throwaway cluster and yield a `postgresql://` URL, or skip."""
    pg_bin = find_pg_bin_dir()
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
