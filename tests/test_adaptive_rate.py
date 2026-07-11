"""적응형 레이트 감속.

429 는 "이 속도가 한계를 넘었다"는 신호다. 그러면 즉시 크게 내리고(반토막),
다시는 429 가 안 나는 지속 가능한 속도에 눌러앉아야 한다. 회복은 넉넉히 느리게 —
한계선을 자주 두드리면 그 자체가 차단 사유가 된다.

내려갈 때 빠르고 크게, 올라갈 때 느리고 조금씩(TCP 혼잡 제어와 같은 비대칭).
시간을 주입해 실제로 안 기다리고 검증한다.
"""

import pytest

from gksave.http import AdaptiveRate


def test_starts_at_base_rate():
    r = AdaptiveRate(base=15.0, clock=lambda: 0.0)
    assert r.current == 15.0


def test_429_halves_the_rate():
    r = AdaptiveRate(base=15.0, clock=lambda: 0.0)
    r.on_rate_limited()
    assert r.current == 7.5


def test_repeated_429_halves_each_time():
    r = AdaptiveRate(base=15.0, clock=lambda: 0.0)
    r.on_rate_limited()   # 7.5
    r.on_rate_limited()   # 3.75
    assert r.current == pytest.approx(3.75)


def test_does_not_drop_below_floor():
    r = AdaptiveRate(base=15.0, floor=2.0, clock=lambda: 0.0)
    for _ in range(20):
        r.on_rate_limited()
    assert r.current == 2.0


def test_recovers_slowly_after_quiet_period():
    t = {"now": 0.0}
    r = AdaptiveRate(base=15.0, recover_step=0.5, recover_interval=300.0,
                     clock=lambda: t["now"])
    r.on_rate_limited()               # 7.5
    assert r.current == 7.5

    t["now"] = 299.0                  # 아직 5분 안 됨
    r.maybe_recover()
    assert r.current == 7.5

    t["now"] = 300.0                  # 5분 경과 → +0.5
    r.maybe_recover()
    assert r.current == 8.0

    t["now"] = 600.0                  # 또 5분 → +0.5
    r.maybe_recover()
    assert r.current == 8.5


def test_recovery_never_exceeds_base():
    t = {"now": 0.0}
    r = AdaptiveRate(base=15.0, recover_step=0.5, recover_interval=300.0,
                     clock=lambda: t["now"])
    r.on_rate_limited()               # 7.5
    t["now"] = 100_000.0              # 아주 오래 조용
    r.maybe_recover()
    assert r.current == 15.0          # base 를 넘지 않는다


def test_429_during_recovery_halves_again():
    t = {"now": 0.0}
    r = AdaptiveRate(base=16.0, recover_step=0.5, recover_interval=300.0,
                     clock=lambda: t["now"])
    r.on_rate_limited()               # 8.0
    t["now"] = 900.0                  # 15분 → +1.5 = 9.5
    r.maybe_recover()
    assert r.current == pytest.approx(9.5)
    r.on_rate_limited()               # 회복 중 429 → 즉시 반토막
    assert r.current == pytest.approx(4.75)


def test_min_interval_reflects_current_rate():
    r = AdaptiveRate(base=10.0, clock=lambda: 0.0)
    assert r.min_interval == pytest.approx(0.1)
    r.on_rate_limited()               # 5.0/s
    assert r.min_interval == pytest.approx(0.2)


def test_zero_base_means_unlimited():
    r = AdaptiveRate(base=0.0, clock=lambda: 0.0)
    assert r.min_interval == 0.0
    r.on_rate_limited()               # 무제한이면 감속도 무의미, 크래시 없어야
    assert r.min_interval == 0.0
