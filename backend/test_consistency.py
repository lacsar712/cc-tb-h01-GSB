"""三路径一致性自动化核对。

规则只有一个事实来源：rules.weigh。
对三档分数（边界 7.0、明显高于 7、明显低于 7）逐条核对三条路径读出同一结论：

  1. 库内结论 —— INSERT 实际绑定的 verdict / note / score 参数
  2. 总表色块 —— GET / 与提交后 HX 增量行里的文案 + 着色 class
  3. 详情说明 —— GET /cuppings/<id> 页里的结论、着色 class、说明

另含：只读会话（observer）写不进去，以及旁路已整段拆除的结构守卫。

环境无 Postgres / Docker 时也可运行：用内存假库回放 SQL，
语义对齐 psycopg2（连接 with 块提交、RealDictCursor、INSERT ... RETURNING）。

运行：cd backend && python3 -m unittest test_consistency -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as webapp  # noqa: E402
from rules import weigh  # noqa: E402

COLUMNS = [
    "id",
    "lot",
    "aroma",
    "taste",
    "liquor",
    "score",
    "verdict",
    "note",
    "created_by",
]

# 三档：边界七分（浮点加权后恰为 7.0）、明显高于七、明显低于七
CASES = [
    ("边界七分", "边界-700", 8.0, 6.4, 7.0),   # 2.4 + 3.2 + 1.4 = 7.0
    ("明显高于七", "高分-880", 9.0, 9.0, 8.0),  # 2.7 + 4.5 + 1.6 = 8.8
    ("明显低于七", "低分-470", 5.0, 4.0, 6.0),  # 1.5 + 2.0 + 1.2 = 4.7
]


class FakeCursor:
    """对齐 RealDictCursor：execute 回放 SQL，fetchall/fetchone 取 dict 行。"""

    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        head = sql.lstrip()
        if head.startswith("INSERT INTO cuppings"):
            lot, aroma, taste, liquor, score, verdict, note, created_by = params
            row = dict(
                zip(
                    COLUMNS,
                    [
                        self.conn.next_id(),
                        lot,
                        aroma,
                        taste,
                        liquor,
                        score,
                        verdict,
                        note,
                        created_by,
                    ],
                )
            )
            self.conn.rows.append(row)
            self.conn.inserted_params.append(params)
            self._rows = [dict(row)] if "RETURNING" in sql.upper() else []
        elif head.startswith("SELECT * FROM cuppings ORDER BY id DESC"):
            self._rows = [
                dict(r) for r in sorted(self.conn.rows, key=lambda r: r["id"], reverse=True)
            ]
        elif head.startswith("SELECT * FROM cuppings WHERE id="):
            (cupping_id,) = params
            self._rows = [dict(r) for r in self.conn.rows if r["id"] == cupping_id]
        else:
            raise AssertionError(f"测试未覆盖的 SQL：{sql!r}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """对齐 psycopg2 连接：with 块正常退出即 commit。"""

    def __init__(self):
        self.rows = []
        self.inserted_params = []
        self.commits = 0
        self._seq = 0

    def next_id(self):
        self._seq += 1
        return self._seq

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *exc):
        if exc_type is None:
            self.commit()
        return False

    def commit(self):
        self.commits += 1


def list_row_pattern(lot):
    return re.compile(
        r"<tr>\s*"
        r"<td>" + re.escape(lot) + r"</td>\s*"
        r"<td>([^<]*)</td>\s*"
        r'<td class="(pass|fail)">([^<]*)</td>\s*'
        r"<td>([^<]*)</td>"
    )


DETAIL_VERDICT = re.compile(r'<p class="(pass|fail)">结论：([^<]*)</p>')
DETAIL_NOTE = re.compile(r"<p>说明：([^<]*)</p>")
DETAIL_SCORE = re.compile(r"<p>加权分：([^<]*)</p>")


class VerdictConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeConn()
        self._orig_db = webapp.db
        webapp.db = lambda: self.fake
        self.client = webapp.app.test_client()

    def tearDown(self):
        webapp.db = self._orig_db

    def login(self, username, password):
        resp = self.client.post(
            "/login", data={"username": username, "password": password}
        )
        self.assertEqual(resp.status_code, 302)

    def stored_row(self, lot):
        matches = [r for r in self.fake.rows if r["lot"] == lot]
        self.assertEqual(len(matches), 1, f"库内批次 {lot} 应恰好一条")
        return matches[0]

    def check_case(self, label, lot, aroma, taste, liquor):
        exp_verdict, exp_note, exp_score = weigh(aroma, taste, liquor)
        exp_class = "pass" if exp_verdict == "通过" else "fail"

        self.login("taster", "tea123456")
        form = {
            "lot": lot,
            "aroma": str(aroma),
            "taste": str(taste),
            "liquor": str(liquor),
        }
        resp = self.client.post("/cuppings", data=form, headers={"HX-Request": "true"})
        self.assertEqual(resp.status_code, 200, f"{label}: 提交应成功")
        fragment = resp.get_data(as_text=True)

        # —— 路径 1：库内结论（INSERT 实际落库参数，未经任何展示层改写）——
        self.assertEqual(len(self.fake.inserted_params), 1, f"{label}: 应只落库一条")
        db_lot, db_aroma, db_taste, db_liquor, db_score, db_verdict, db_note, db_by = (
            self.fake.inserted_params[0]
        )
        self.assertEqual(db_lot, lot)
        self.assertEqual((db_aroma, db_taste, db_liquor), (aroma, taste, liquor))
        self.assertEqual(db_by, "taster")
        self.assertEqual(db_score, exp_score, f"{label}: 库内分数")
        self.assertEqual(db_verdict, exp_verdict, f"{label}: 库内结论须与判定逐字一致")
        self.assertEqual(db_note, exp_note, f"{label}: 库内说明须与判定逐字一致")

        # —— 路径 2a：总表色块（整表 GET /）——
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        m = list_row_pattern(lot).search(home_html)
        self.assertIsNotNone(m, f"{label}: 总表找不到批次行")
        list_score, list_class, list_verdict, list_note = m.groups()
        self.assertEqual(list_score, str(exp_score), f"{label}: 总表分数")
        self.assertEqual(list_class, exp_class, f"{label}: 总表色块")
        self.assertEqual(list_verdict, exp_verdict, f"{label}: 总表结论")
        self.assertEqual(list_note, exp_note, f"{label}: 总表说明")

        # —— 路径 2b：提交后 HX 增量行（真实插进总表的那一段）——
        m = list_row_pattern(lot).search(fragment)
        self.assertIsNotNone(m, f"{label}: HX 增量行格式不对")
        hx_score, hx_class, hx_verdict, hx_note = m.groups()
        self.assertEqual((hx_score, hx_class, hx_verdict, hx_note),
                         (str(exp_score), exp_class, exp_verdict, exp_note),
                         f"{label}: HX 增量行须与判定一致")

        # —— 路径 3：详情说明 ——
        cupping_id = self.stored_row(lot)["id"]
        detail = self.client.get(f"/cuppings/{cupping_id}")
        self.assertEqual(detail.status_code, 200)
        detail_html = detail.get_data(as_text=True)
        dv = DETAIL_VERDICT.search(detail_html)
        dn = DETAIL_NOTE.search(detail_html)
        ds = DETAIL_SCORE.search(detail_html)
        self.assertTrue(dv and dn and ds, f"{label}: 详情页结构不完整")
        detail_class, detail_verdict = dv.groups()
        self.assertEqual(ds.group(1), str(exp_score), f"{label}: 详情分数")
        self.assertEqual(detail_class, exp_class, f"{label}: 详情色块")
        self.assertEqual(detail_verdict, exp_verdict, f"{label}: 详情结论")
        self.assertEqual(dn.group(1), exp_note, f"{label}: 详情说明")

        # —— 三条路径逐字互校 ——
        self.assertEqual({db_verdict, list_verdict, hx_verdict, detail_verdict},
                         {exp_verdict}, f"{label}: 四处结论必须逐字相同")
        self.assertEqual({db_note, list_note, hx_note, dn.group(1)},
                         {exp_note}, f"{label}: 四处说明必须逐字相同")
        self.assertEqual({list_class, hx_class, detail_class},
                         {exp_class}, f"{label}: 三处色块必须相同")

        # 旁路痕迹不得出现在任何渲染页面
        for html in (home_html, fragment, detail_html):
            self.assertNotIn("旁路", html)
            self.assertNotIn("bypass", html)
        # 落库后确实提交（create 显式 commit，with 块退出再提交一次，均为正常）
        self.assertGreaterEqual(self.fake.commits, 1)

    def test_边界七分_三路径一致(self):
        label, lot, a, t, l = CASES[0]
        self.check_case(label, lot, a, t, l)
        # 放行线是 >= 7：摸到 7.0 即通过
        self.assertEqual(weigh(7.0, 7.0, 7.0), ("通过", "加权分达到放行线", 7.0))

    def test_明显高于七_三路径一致(self):
        label, lot, a, t, l = CASES[1]
        self.check_case(label, lot, a, t, l)

    def test_明显低于七_三路径一致(self):
        label, lot, a, t, l = CASES[2]
        self.check_case(label, lot, a, t, l)

    def test_非HX提交走整页跳转(self):
        self.login("taster", "tea123456")
        resp = self.client.post(
            "/cuppings",
            data={"lot": "普通-710", "aroma": "7", "taste": "7", "liquor": "7.5"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")
        verdict, note, score = weigh(7.0, 7.0, 7.5)
        self.assertEqual(
            self.fake.inserted_params[0][5:7], (verdict, note)
        )


class ReadOnlySessionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeConn()
        self._orig_db = webapp.db
        webapp.db = lambda: self.fake
        self.client = webapp.app.test_client()

    def tearDown(self):
        webapp.db = self._orig_db

    FORM = {"lot": "越权-900", "aroma": "9", "taste": "9", "liquor": "9"}

    def test_observer普通提交_403且不落库(self):
        self.client.post("/login", data={"username": "observer", "password": "look123456"})
        resp = self.client.post("/cuppings", data=self.FORM)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("仅审评员", resp.get_data(as_text=True))
        self.assertEqual(self.fake.rows, [])
        self.assertEqual(self.fake.inserted_params, [])

    def test_observer伪造HX头_同样403且不落库(self):
        self.client.post("/login", data={"username": "observer", "password": "look123456"})
        resp = self.client.post(
            "/cuppings", data=self.FORM, headers={"HX-Request": "true"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.fake.rows, [])

    def test_未登录提交_被跳转登录且不落库(self):
        resp = self.client.post("/cuppings", data=self.FORM)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.fake.rows, [])

    def test_observer可只读总表(self):
        # 先由审评员落一条通过记录
        writer = webapp.app.test_client()
        writer.post("/login", data={"username": "taster", "password": "tea123456"})
        writer.post(
            "/cuppings",
            data={"lot": "只读-880", "aroma": "8", "taste": "8", "liquor": "7"},
            headers={"HX-Request": "true"},
        )
        # observer 登录查看，页面里仍是通过/绿色
        self.client.post(
            "/login", data={"username": "observer", "password": "look123456"}
        )
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("只读-880", html)
        self.assertIn('class="pass">通过</td>', html)
        self.assertNotIn("旁路", html)


class BypassRemovedTests(unittest.TestCase):
    """结构守卫：旁路整段拆除，三处挂载点不得复活。"""

    BACKEND = os.path.dirname(os.path.abspath(__file__))

    def test_旁路模块文件已删除(self):
        self.assertFalse(
            os.path.exists(os.path.join(self.BACKEND, "weigh_force_fail.py")),
            "weigh_force_fail.py 必须整段删除，而非留空壳",
        )

    def test_app不再引用旁路(self):
        with open(os.path.join(self.BACKEND, "app.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("weigh_force_fail", src)
        self.assertNotIn("polish", src)
        # 落库参数必须直接来自 weigh()
        self.assertIn("verdict, note, score = weigh(", src)

    def test_总表行不再硬编码fail色块(self):
        with open(
            os.path.join(self.BACKEND, "templates", "_row.html"), encoding="utf-8"
        ) as fh:
            src = fh.read()
        self.assertNotIn('class="fail">{{ row.verdict }}', src)
        self.assertIn('class="{% if row.verdict == \'通过\' %}pass{% else %}fail{% endif %}"', src)

    def test_全仓无旁路残留引用(self):
        import pathlib

        this_file = pathlib.Path(__file__).resolve()
        root = pathlib.Path(self.BACKEND)
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".html"}:
                continue
            if path.resolve() == this_file:
                continue  # 本守卫测试自身会提到旁路名字
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("weigh_force_fail", text, f"{path} 仍引用旁路模块")
            self.assertNotIn("够线强制不通过", text, f"{path} 仍含旁路字样")


if __name__ == "__main__":
    unittest.main(verbosity=2)
