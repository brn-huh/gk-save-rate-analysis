"""DB 쓰기 락 충돌은 트레이스백이 아니라 읽을 수 있는 메시지로 알린다.

DuckDB 단일 파일이라 쓰기 프로세스는 하나뿐이다. collect 중에 build 를 돌리거나
두 collect 를 겹쳐 돌리면 `_duckdb.IOException: Could not set lock ...` 이 그대로
터진다. 데이터는 안전하지만(연결 실패라 아무것도 안 씀) 원인 파악이 어렵다.

락은 프로세스 간에만 이렇게 동작한다. 같은 프로세스에서 다시 열면 duckdb 가
ConnectionException 을 준다 — 그래서 별도 프로세스로 락을 잡고 검증한다.
"""

import dataclasses
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from gksave import db

_HOLDER = """
import duckdb, sys, time
con = duckdb.connect(sys.argv[1])
con.execute("CREATE TABLE IF NOT EXISTS t(x INT)")
print("locked", flush=True)
time.sleep(30)
"""


def _hold_lock(path: Path) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "locked"   # 락 획득까지 대기
    return p


def test_locked_db_raises_readable_error(tmp_path):
    path = tmp_path / "lock.duckdb"
    holder = _hold_lock(path)
    try:
        settings = dataclasses.replace(db.DEFAULT, data_dir=tmp_path, db_name="lock.duckdb")
        with pytest.raises(db.DbLockedError) as e:
            db.connect(settings)
        msg = str(e.value)
        assert "다른 프로세스" in msg          # 무슨 일인지
        assert str(path) in msg                # 어느 파일인지
    finally:
        holder.kill()
        holder.wait()


def test_read_only_also_blocked_by_writer(tmp_path):
    """읽기 전용도 쓰기 락과 공존하지 못한다 — build --full 중 조회가 막히는 이유."""
    path = tmp_path / "ro.duckdb"
    holder = _hold_lock(path)
    try:
        settings = dataclasses.replace(db.DEFAULT, data_dir=tmp_path, db_name="ro.duckdb")
        with pytest.raises(db.DbLockedError):
            db.connect(settings, read_only=True)
    finally:
        holder.kill()
        holder.wait()


def test_connect_works_when_lock_is_free(tmp_path):
    settings = dataclasses.replace(db.DEFAULT, data_dir=tmp_path, db_name="free.duckdb")
    con = db.connect(settings)
    assert con.execute("SELECT 1").fetchone()[0] == 1
    con.close()


def test_cli_reports_lock_without_traceback(monkeypatch):
    """락에 걸리면 CLI 는 트레이스백 대신 안내 메시지로 끝나야 한다.

    실 DB 를 건드리지 않도록 connect 지점만 갈아끼운다. 락 자체의 동작은 위에서 검증했다.
    """
    from gksave import cli

    def _locked(*a, **k):
        raise db.DbLockedError("다른 프로세스가 DB 를 쓰고 있다: /x/y.duckdb")

    monkeypatch.setattr(cli, "connect", _locked)

    with pytest.raises(SystemExit) as e:
        cli.main(["build"])
    assert "다른 프로세스" in str(e.value)
    assert "Traceback" not in str(e.value)


def test_other_io_errors_are_not_swallowed(tmp_path):
    """락 이외의 IO 에러를 DbLockedError 로 뭉개면 진짜 원인을 가린다."""
    (tmp_path / "notadb.duckdb").write_text("이건 duckdb 파일이 아니다")
    settings = dataclasses.replace(db.DEFAULT, data_dir=tmp_path, db_name="notadb.duckdb")
    with pytest.raises(duckdb.IOException) as e:
        db.connect(settings)
    assert not isinstance(e.value, db.DbLockedError)
