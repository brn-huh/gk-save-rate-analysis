"""수집 롤링 창.

COLLECT_MIN_DATE("2026-03-26")는 죽은 설정이었다 — 실제 데이터는 6/1 부터이고
지평선은 날짜가 아니라 user_pages=3(유저당 최근 300경기)이 정한다. 롤링 창으로 바꿨다.

수집 창(35일)은 통계 창(30일)보다 넓어야 한다. 좁으면 창 경계에서 데이터가 빈다.
"""

from datetime import datetime, timedelta, timezone

from gksave import config
from gksave.collect import _default_since


def test_dead_min_date_config_is_gone():
    assert not hasattr(config, "COLLECT_MIN_DATE")


def test_collect_window_is_wider_than_stats_window():
    # update.sh 는 export --days 30 을 쓴다. 수집 창은 그보다 넓어야 한다.
    assert config.COLLECT_WINDOW_DAYS > 30


def test_default_since_is_rolling_from_now():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = _default_since()
    expected = now - timedelta(days=config.COLLECT_WINDOW_DAYS)
    assert abs((since - expected).total_seconds()) < 5
    assert since.tzinfo is None  # match_date 와 같은 naive UTC 기준
