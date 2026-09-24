"""端到端一致性核对：库内结论 / 总表色块 / 详情说明必须与 weigh() 判定逐字一致。

本地无需 PostgreSQL：用实现 psycopg2 协议的内存假库接管 app.db，
INSERT/SELECT 行为与 seed.py 的表结构一致，行对象同样支持属性访问。
"""

import re

import pytest

import app as app_module
from rules import weigh

PASS = "通过"
FAIL = "不通过"
PASS_NOTE = "加权分达到放行线"
FAIL_NOTE = "加权分低于放行线"

# (用例名, 香气, 滋味, 汤色, 期望加权分, 期望结论, 期望说明)
CASES = [
    ("边界七分", 7, 7, 7, 7.0, PASS, PASS_NOTE),
    ("明显高于七", 9, 9, 9, 9.0, PASS, PASS_NOTE),
    ("明显低于七", 4, 5, 6, 4.9, FAIL, FAIL_NOTE),
]


class RealDictRow(dict):
    """与 psycopg2.extras.RealDictRow 同款：字典同时支持属性访问。"""

    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = []

    def execute(self, sql, params=None):
        s = sql.strip()
        params = params or ()
        if s.startswith("INSERT INTO cuppings"):
            lot, aroma, taste, liquor, score, verdict, note, created_by = params
            row = RealDictRow(
                id=len(self.store) + 1,
                lot=lot,
                aroma=aroma,
                taste=taste,
                liquor=liquor,
                score=score,
                verdict=verdict,
                note=note,
                created_by=created_by,
            )
            self.store.append(row)
            self._result = [row]
        elif "FROM cuppings WHERE id=%s" in s:
            self._result = [r for r in self.store if r["id"] == params[0]]
        else:  # SELECT * FROM cuppings ORDER BY id DESC
            self._result = sorted(self.store, key=lambda r: r["id"], reverse=True)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.store)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def store(monkeypatch):
    rows = []
    monkeypatch.setattr(app_module, "db", lambda: FakeConn(rows))
    return rows


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def login(client, username, password):
    resp = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return client


def tone_of(verdict):
    return "pass" if verdict == PASS else "fail"


def list_verdict(html):
    # 总表行内唯一一个带 pass/fail 色块的单元格
    m = re.search(r'<td class="(?:pass|fail)">([^<]+)</td>', html)
    assert m, f"总表行缺少结论色块：{html!r}"
    return m.group(1)


def list_note(html):
    # 无 class 的 td 依次为：批次、加权分、说明（第四个 td 内含 <a>）
    cells = re.findall(r"<td>([^<]*)</td>", html)
    assert len(cells) >= 3, f"总表行结构异常：{html!r}"
    return cells[2]


def detail_verdict(html):
    m = re.search(r'<p class="(?:pass|fail)">结论：([^<]+)</p>', html)
    assert m, f"详情页缺少结论色块：{html!r}"
    return m.group(1)


def detail_note(html):
    m = re.search(r"<p>说明：([^<]+)</p>", html)
    assert m, f"详情页缺少说明：{html!r}"
    return m.group(1)


@pytest.mark.parametrize("name,aroma,taste,liquor,exp_score,exp_verdict,exp_note", CASES, ids=[c[0] for c in CASES])
def test_three_paths_match_judgement(client, store, name, aroma, taste, liquor, exp_score, exp_verdict, exp_note):
    # 路径 0：判定函数本身
    verdict, note, score = weigh(aroma, taste, liquor)
    assert (score, verdict, note) == (exp_score, exp_verdict, exp_note)

    login(client, "taster", "tea123456")
    resp = client.post(
        "/cuppings",
        data={"lot": name, "aroma": aroma, "taste": taste, "liquor": liquor},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    fragment = resp.get_data(as_text=True)
    tone = tone_of(verdict)

    # 路径 1：落库结论（写库前不得被任何旁路改写）
    assert len(store) == 1
    db_row = store[0]
    assert (db_row["score"], db_row["verdict"], db_row["note"]) == (score, verdict, note)

    # 路径 2：总表色块（HX 新增行片段 + 完整总表整页渲染各核一遍）
    assert f'<td class="{tone}">{verdict}</td>' in fragment
    assert f"<td>{note}</td>" in fragment
    home = client.get("/").get_data(as_text=True)
    assert f'<td class="{tone}">{verdict}</td>' in home
    assert f"<td>{note}</td>" in home

    # 路径 3：详情说明
    detail = client.get(f"/cuppings/{db_row['id']}").get_data(as_text=True)
    assert f'<p class="{tone}">结论：{verdict}</p>' in detail
    assert f"<p>说明：{note}</p>" in detail

    # 三条路径读出的结论、说明逐字一致，且色块方向与结论一致
    assert {db_row["verdict"], list_verdict(fragment), list_verdict(home), detail_verdict(detail)} == {verdict}
    assert {db_row["note"], list_note(fragment), list_note(home), detail_note(detail)} == {note}


def test_reader_cannot_submit(client, store):
    login(client, "observer", "look123456")

    # 只读会话总表不渲染提交表单
    home = client.get("/").get_data(as_text=True)
    assert 'action="/cuppings"' not in home
    assert "新开一轮审评" not in home

    # 直接 POST 被拒，且没有任何记录落库
    resp = client.post(
        "/cuppings",
        data={"lot": "越权批次", "aroma": 9, "taste": 9, "liquor": 9},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 403
    assert "仅审评员可提交拼配审评" in resp.get_data(as_text=True)
    assert store == []


def test_anonymous_cannot_submit(client, store):
    resp = client.post(
        "/cuppings",
        data={"lot": "匿名批次", "aroma": 9, "taste": 9, "liquor": 9},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert store == []
