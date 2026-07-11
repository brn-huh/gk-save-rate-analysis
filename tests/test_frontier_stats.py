"""수집 대기열(frontier) 요약.

수집 시작·끝에 대기 유저 수를 또렷하게 보여준다. 대기열은 스노우볼이 새 유저를
발견해 수집 중 오히려 늘어나므로, 시작→끝 변화가 그 자체로 정보다.
"""

from gksave.collect import frontier_counts
from gksave.db import connect_memory


def _seed(con, pending=0, done=0):
    for i in range(pending):
        con.execute("INSERT INTO frontier (ouid, state) VALUES (?, 'pending')", [f"p{i}"])
    for i in range(done):
        con.execute("INSERT INTO frontier (ouid, state) VALUES (?, 'done')", [f"d{i}"])


def test_counts_pending_and_done():
    con = connect_memory()
    _seed(con, pending=5, done=3)
    c = frontier_counts(con)
    assert c.pending == 5
    assert c.done == 3
    assert c.total == 8


def test_empty_frontier_is_all_zero():
    con = connect_memory()
    c = frontier_counts(con)
    assert (c.pending, c.done, c.total) == (0, 0, 0)


def test_done_pct_guards_zero_total():
    con = connect_memory()
    c = frontier_counts(con)
    assert c.done_pct == 0.0        # 0/0 크래시 없이 0


def test_done_pct_computed():
    con = connect_memory()
    _seed(con, pending=3, done=1)
    assert frontier_counts(con).done_pct == 25.0
