"""raw_match.payload 는 압축 BLOB 이다.

수집기가 매치마다 20.8KB JSON 을 쓰는 동안 in-flight 요청이 0이 되므로, 쓰기량이
곧 수집 처리량이다. 컬럼 타입이 JSON 으로 되돌아가면 그 이득이 통째로 사라진다.
"""

from gksave import db
from gksave.codec import decode_payload, encode_payload


def _payload_type(con):
    return con.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE table_name='raw_match' AND column_name='payload'"
    ).fetchone()[0]


def test_payload_column_is_blob():
    con = db.connect_memory()
    assert _payload_type(con) == "BLOB"


def test_compressed_payload_roundtrips_through_the_column():
    con = db.connect_memory()
    detail = {"matchId": "abc", "설명": "한글 보존"}
    con.execute(
        "INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
        ["abc", encode_payload(detail)],
    )
    (stored,) = con.execute("SELECT payload FROM raw_match WHERE match_id='abc'").fetchone()
    assert decode_payload(stored) == detail


def test_read_only_connect_does_not_apply_schema(tmp_path):
    """T6 마이그레이션은 원본을 read_only 로 연다 — 그때 스키마가 덮이면 안 된다."""
    import dataclasses

    import duckdb

    duckdb.connect(str(tmp_path / "empty.duckdb")).close()  # 테이블 없는 빈 DuckDB 파일

    settings = dataclasses.replace(db.DEFAULT, data_dir=tmp_path, db_name="empty.duckdb")
    con = db.connect(settings, read_only=True)
    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    assert "raw_match" not in tables  # SCHEMA 가 적용되지 않았다
