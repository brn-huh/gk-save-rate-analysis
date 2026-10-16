"""긴 작업의 로그 출력과 정상·오류 종료 시 타이머 정리."""

from threading import Event

import pytest

from gksave import progress


def test_heartbeat_reports_current_stage_and_stops_after_completion(monkeypatch):
    messages, received = [], Event()

    def capture(message):
        messages.append(message)
        if "GSAx 집계" in message and "실행 중" in message:
            received.set()

    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(progress, "log", capture)
    with progress.progress("통계 export") as report:
        report("GSAx 집계")
        assert received.wait(2)
    assert messages[0] == "통계 export: 시작"
    assert messages[-1].startswith("통계 export: 완료 · 경과 ")
    count = len(messages)
    Event().wait(0.01)
    assert len(messages) == count


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_failure_is_not_reported_as_success_and_stops_heartbeat(monkeypatch, failure):
    messages = []
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(progress, "log", messages.append)
    with pytest.raises(failure):
        with progress.progress("상세 조회"):
            raise failure("synthetic failure")
    assert messages[-1].startswith("상세 조회: 중단 · 경과 ")
    assert not any("완료" in message for message in messages)
    count = len(messages)
    Event().wait(0.01)
    assert len(messages) == count
