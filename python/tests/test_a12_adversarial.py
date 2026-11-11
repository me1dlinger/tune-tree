"""A1/A2 对抗性审查用例（红队）。

本文件只做两件事：
1. **正例**：把「审查时怀疑、但实测站得住」的点钉死，防止后续被改坏；
2. 曾经用 xfail(strict=True) 钉住的 8 条缺陷**已全部修复**，标记已移除，用例转为回归测试。
   strict 语义：一旦有人修好了，用例会变成 XPASS → 套件直接失败，
   迫使维护者删掉 xfail 标记。

审查快照（工作区未提交）：
    routes.py          a75f6cc9
    models/db.py       9008c360
    artist_repo        0d3266bd
    similarity_repo    cedfef9b
    similarity_service 2c559d70
    scan_service       75790a2f
    artist-stats.js    040e5936
    artist.js          e076303f
    main.css           d1ca8744
    index.html         dad0ab47

已独立验证「没问题」的点（对应下面的正例）：
- 老库重建 artists 表：13 列清单与 CREATE TABLE 一致，用户标记不丢，
  sqlite_sequence 不重置（AUTOINCREMENT 不重号）；
- 5 个 ALTER 列与 CREATE TABLE 的类型/DEFAULT 完全一致；update_artist 白名单齐全；
- `confirmed` 配对不会因为「贪心配对抢占」而重复出现（get_all_artist_names 恒按 id 升序，
  所以 seen_pairs 与反馈表的规范有序对同构，去重真的生效）；
- scan 只在磁盘上真有 cover.jpg 时才解开标记，没有封面时标记不动；
- 新增 9 个接口全部带 @require_auth；
- 定时任务/扫描/曲目刮削都不会去抓艺术家头像（不存在 A1 的定时绕过路径）。
"""

import re
import sqlite3
import time
from pathlib import Path

import pytest

from models.db import get_db
from repository.artist_repository import (
    ensure_artist,
    get_artist_by_id,
    get_skipped_cover_artists,
    set_artist_cover_skip,
    update_artist,
)
from repository import similarity_repository as sr

PYTHON_DIR = Path(__file__).resolve().parents[1]
INDEX_HTML = PYTHON_DIR / "static" / "index.html"
ARTIST_STATS_JS = PYTHON_DIR / "static" / "js" / "modules" / "artist-stats.js"

NEW_COVER_COLUMNS = (
    "cover_skip",
    "cover_skip_reason",
    "cover_fetch_attempts",
    "cover_fetch_failed_at",
    "cover_last_source",
)

DEFAULT_THRESHOLDS = {"short": 0.95, "len3": 0.85, "len5": 0.80, "long": 0.75}


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """阈值是进程内模块级可变状态：跑完把现场还原，别污染其它文件。"""
    from services import similarity_service as ss

    yield
    ss.SIMILARITY_THRESHOLDS.clear()
    ss.SIMILARITY_THRESHOLDS.update(dict(ss.DEFAULT_SIMILARITY_THRESHOLDS))


# ════════════════════════════════════════════════════════════════════
# 一、正例：怀疑过、但实测没问题的点
# ════════════════════════════════════════════════════════════════════


A1_A2_ENDPOINTS = [
    ("post", "/api/artists/1/cover-skip", {"skip": True}),
    ("post", "/api/artists/cover-skip/batch", {"artist_ids": [1], "skip": True}),
    ("get", "/api/artists/without-cover", None),
    ("get", "/api/artists/cover-skipped", None),
    ("get", "/api/stats/similar-artists/thresholds", None),
    ("put", "/api/stats/similar-artists/thresholds", {"short": 0.9}),
    (
        "post",
        "/api/stats/similar-artists/feedback",
        {"artist_a_id": 1, "artist_b_id": 2, "verdict": "dismissed"},
    ),
    ("delete", "/api/stats/similar-artists/feedback/1/2", None),
    ("get", "/api/stats/similar-artists/feedback", None),
]


@pytest.mark.parametrize("method,path,body", A1_A2_ENDPOINTS, ids=lambda v: str(v))
def test_new_endpoints_all_require_auth(flask_app, method, path, body):
    """A1/A2 新增的 9 个接口必须每一个都带鉴权（漏一个就是越权写库）。"""
    anon = flask_app.test_client()  # 注意：不带 X-Token
    resp = getattr(anon, method)(path, json=body) if body is not None else getattr(
        anon, method
    )(path)
    assert resp.status_code == 401, (method, path, resp.status_code, resp.get_data())
    assert resp.get_json()["error"] == "unauthorized"


def test_thresholds_are_really_persisted_not_just_process_state(client, app_ctx):
    """阈值 PUT 必须落库；只改进程内 dict 的实现会在这里露馅。

    做法：PUT 之后把模块状态手动清回默认值（模拟重启 / 另一个 worker），
    再 GET —— 此时只能靠 scan_meta 里的持久化配置把值读回来。
    """
    from services import similarity_service as ss

    payload = {"short": 0.9, "len3": 0.7, "len5": 0.6, "long": 0.5}
    assert client.put(
        "/api/stats/similar-artists/thresholds", json=payload
    ).status_code == 200

    ss.SIMILARITY_THRESHOLDS.clear()
    ss.SIMILARITY_THRESHOLDS.update(dict(DEFAULT_THRESHOLDS))
    assert ss.get_thresholds() == DEFAULT_THRESHOLDS  # 确认现场真的被清干净

    body = client.get("/api/stats/similar-artists/thresholds").get_json()
    assert body == payload, "阈值没有真正落库（只活在进程内存里）"


def test_confirmed_pair_appears_exactly_once(client, app_ctx, library):
    """confirmed 配对不能因为「回显补充」而重复出现两张一模一样的卡片。

    审查怀疑点：find_similar_artists 里的 seen_pairs 用的是 (artist_a.id, artist_b.id)，
    而反馈表存的是规范有序对 (小id, 大id)。若两者顺序口径不同，去重就会失效并产生重复项。
    实测顺序一致（get_all_artist_names 恒 ORDER BY id），故写成正例。
    """
    from repository.artist_repository import ensure_artist as mk

    low_id = mk("zzz 低 id", library_id=library["id"])
    high_id = mk("aaa 高 id", library_id=library["id"])
    assert low_id < high_id

    resp = client.post(
        "/api/stats/similar-artists/feedback",
        json={
            "artist_a_id": high_id,
            "artist_b_id": low_id,
            "verdict": "confirmed",
        },
    )
    assert resp.status_code == 200, resp.get_json()

    groups = client.get("/api/stats/similar-artists").get_json()
    pairs = [
        tuple(sorted((g["artist_a"]["id"], g["artist_b"]["id"]))) for g in groups
    ]
    assert pairs.count((low_id, high_id)) == 1, f"同一对出现 {pairs.count((low_id, high_id))} 次: {pairs}"


def test_scan_keeps_skip_when_no_cover_on_disk(client, app_ctx, library):
    """没有 cover.jpg 时，扫描绝不能动用户的「不再获取」标记。"""
    from services.scan_service import _ensure_covers

    aid = ensure_artist("扫描不该动我", library_id=library["id"])
    assert client.post(
        f"/api/artists/{aid}/cover-skip", json={"skip": True}
    ).status_code == 200

    _ensure_covers(library["path"], library_id=library["id"])

    row = get_artist_by_id(aid)
    assert row["cover_skip"] == 1
    assert row["cover_skip_reason"] == "user"
    assert [r["id"] for r in get_skipped_cover_artists()] == [aid]


def test_legacy_rebuild_keeps_marker_columns_and_rowid(app_ctx, db_path):
    """老库升级（列级 UNIQUE → 重建表）后：标记存活 + AUTOINCREMENT 不重号。

    比现有用例更狠的地方：
    - 5 个新列全部预置好（模拟「已 ALTER 出列、用户已标记、但表还没重建」的中间态）；
    - 校验 sqlite_sequence 未被重置（重建时若丢 sequence，新艺术家会撞 id）。
    """
    import models.db as db_module

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS artists;
        CREATE TABLE artists (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            name_normalized TEXT NOT NULL,
            dir_name        TEXT NOT NULL UNIQUE,
            cover_path      TEXT,
            library_id      INTEGER,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL,
            cover_skip            INTEGER DEFAULT 0,
            cover_skip_reason     TEXT,
            cover_fetch_attempts  INTEGER DEFAULT 0,
            cover_fetch_failed_at REAL,
            cover_last_source     TEXT
        );
        CREATE UNIQUE INDEX idx_artists_name_norm ON artists(name_normalized);
        """
    )
    now = time.time()
    conn.execute(
        "INSERT INTO artists (name,name_normalized,dir_name,created_at,updated_at)"
        " VALUES ('甲','甲','jia',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO artists (name,name_normalized,dir_name,created_at,updated_at,"
        " cover_skip,cover_skip_reason,cover_fetch_attempts,cover_fetch_failed_at,"
        " cover_last_source) VALUES ('乙','乙','yi',?,?,1,'user',4,?,'netease')",
        (now, now, now),
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND name LIKE 'sqlite_autoindex_artists_%'"
    ).fetchall(), "夹具没造出列级 UNIQUE（autoindex），重建分支不会被触发"
    before = conn.execute(
        "SELECT id,name,cover_skip,cover_skip_reason,cover_fetch_attempts,"
        "cover_fetch_failed_at,cover_last_source FROM artists ORDER BY id"
    ).fetchall()
    seq_before = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='artists'"
    ).fetchone()[0]
    conn.close()

    db_module.init_db()

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info('artists')").fetchall()]
        missing = [c for c in NEW_COVER_COLUMNS if c not in cols]
        assert not missing, f"重建 artists 表丢了新列: {missing}"
        after = conn.execute(
            "SELECT id,name,cover_skip,cover_skip_reason,cover_fetch_attempts,"
            "cover_fetch_failed_at,cover_last_source FROM artists ORDER BY id"
        ).fetchall()
        assert before == after, f"重建丢数据：\n before={before}\n after={after}"
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name LIKE 'sqlite_autoindex_artists_%'"
        ).fetchall(), "列级唯一约束没被移除"
        assert conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='artists'"
        ).fetchone()[0] == seq_before, "重建后 sqlite_sequence 被重置，新艺术家会撞 id"
        conn.execute(
            "INSERT INTO artists (name,name_normalized,dir_name,created_at,updated_at)"
            " VALUES ('丙','丙','bing',1,1)"
        )
        assert conn.execute(
            "SELECT id FROM artists WHERE dir_name='bing'"
        ).fetchone()[0] > max(r[0] for r in before), "重建后新行 id 与旧行冲突"
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# 二、曾经的缺陷（现已全部修复，作为回归钉保留）
#
# 这里的用例原本是 xfail(strict=True)：修好一条就会 XPASS 变红，强制维护者回来删标记。
# 8 条全部修复后标记已移除，现在它们是普通的回归测试（改坏就红）。
# ════════════════════════════════════════════════════════════════════


def test_invalid_saved_thresholds_fall_back_to_defaults(client, app_ctx):
    """最小复现：PUT 合法阈值 → 把 scan_meta 改成非法 JSON → GET 应回落到默认值。

    实测：api/routes.py:2440 `_load_similarity_thresholds` 的 except 分支返回
    `get_similarity_thresholds()`（当前进程内状态），而不是
    `set_similarity_thresholds(dict(DEFAULT_SIMILARITY_THRESHOLDS))`；
    而紧邻的 `if not raw` 分支返回的才是默认值，日志里也写着「已回退默认值」。

    后果：
    - 多 worker / 重启后行为不一致：刚启动的 worker 返回默认值，跑过配置的 worker 返回旧值；
    - 用户界面上阈值显示为一个从未保存过的值。
    修法：except 分支改成 `return set_similarity_thresholds(dict(DEFAULT_SIMILARITY_THRESHOLDS))`。
    """
    payload = {"short": 0.9, "len3": 0.7, "len5": 0.6, "long": 0.5}
    assert client.put(
        "/api/stats/similar-artists/thresholds", json=payload
    ).status_code == 200

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO scan_meta (key, value) VALUES"
        " ('similarity_thresholds', '{\"short\": 99}')"
    )
    db.commit()

    body = client.get("/api/stats/similar-artists/thresholds").get_json()
    assert body == DEFAULT_THRESHOLDS, f"非法配置没有回退默认值，拿到 {body}"

    # 另一个「干净进程」视角：清空模块状态后同一份坏配置又变成默认值 → 一致性破裂
    from services import similarity_service as ss

    ss.SIMILARITY_THRESHOLDS.clear()
    ss.SIMILARITY_THRESHOLDS.update(dict(DEFAULT_THRESHOLDS))
    assert client.get("/api/stats/similar-artists/thresholds").get_json() == DEFAULT_THRESHOLDS


def test_skip_string_false_does_not_mark(client, app_ctx, library):
    """最小复现：POST /api/artists/<id>/cover-skip {"skip": "false"} → 艺术家被标记。

    api/routes.py:1064 `skip = bool(data.get("skip", True))`：
    任何非空字符串都是 True，所以 "false" / "0" / "no" 都会把艺术家标记为「不再获取」。
    （同一函数里 `reason` 做了 .strip()，说明入参本来就是不可信的外部 JSON。）
    修法：只接受真正的布尔（或 0/1、"true"/"false" 显式解析），其余返回 400。
    """
    aid = ensure_artist("字符串 false", library_id=library["id"])
    resp = client.post(f"/api/artists/{aid}/cover-skip", json={"skip": "false"})
    assert resp.status_code == 200, resp.get_json()

    assert get_artist_by_id(aid)["cover_skip"] == 0, "skip='false' 竟然把艺术家标记了"
    assert get_skipped_cover_artists() == []


def test_include_skipped_string_false_does_not_bypass_mark(
    client, app_ctx, library, monkeypatch
):
    """最小复现：批量刮削传 {"include_skipped": "false"} → 已标记的艺术家照样被抓取。

    api/routes.py:2567 `include_skipped = bool(data.get("include_skipped"))`。
    这是 A1「不会被绕过」的直接反例：客户端只要把 false 写成字符串，
    保护就失效（前端目前传真布尔，所以是隐患而不是现网故障）。
    修法：同 test_skip_string_false_does_not_mark，做严格布尔解析。
    """
    import api.routes as routes

    aid = ensure_artist("不该被抓取", library_id=library["id"])
    assert client.post(
        f"/api/artists/{aid}/cover-skip", json={"skip": True}
    ).status_code == 200

    calls = []
    monkeypatch.setattr(
        routes.scraper,
        "scrape_artist_avatar",
        lambda name: (calls.append(name), (None, "netease"))[1],
    )

    resp = client.post(
        "/api/artists/batch-scrape-covers",
        json={"artist_ids": [aid], "include_skipped": "false"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert calls == [], "已标记「不再获取」的艺术家被 include_skipped='false' 绕过去了"


def test_cover_skipped_count_keeps_stats_identity(client, app_ctx, library):
    """最小复现：给一个已有封面的艺术家打标记 → 统计数字互相矛盾。

    repository/artist_repository.py:191 的 cover_skipped 只按 cover_skip=1 计数，
    不看 cover_path；而 without_cover / without_cover_active 都要求无封面。
    于是恒等式 `without_cover == without_cover_active + cover_skipped` 破裂。

    用户可见后果（artist-stats.js:432 的标题）：
        「无封面艺术家 TOP 10（另有 1 位已标记不再获取）」+ 正文「所有艺术家都有封面」。
    修法：cover_skipped 加上 `AND (cover_path IS NULL OR cover_path='')`，
    或者前端文案改成「已标记不再获取（含已有封面）」。
    """
    aid = ensure_artist("有封面但被标记", library_id=library["id"])
    cover = Path(library["path"]) / "有封面但被标记" / "cover.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\xff\xd8\xff\xd9")
    update_artist(aid, cover_path=str(cover))

    assert client.post(
        f"/api/artists/{aid}/cover-skip", json={"skip": True}
    ).status_code == 200

    stats = client.get("/api/stats/artists").get_json()
    assert stats["with_cover"] == 1
    assert stats["without_cover"] == 0
    assert (
        stats["without_cover"]
        == stats["without_cover_active"] + stats["cover_skipped"]
    ), f"统计口径破裂: {stats}"


def test_feedback_rows_pruned_when_artists_disappear(client, app_ctx, library):
    """最小复现：标记一对 → 扫描清理掉两个孤立艺术家 → 反馈行仍在表里。

    scan_service.py:723 的 `_cleanup_orphaned_artists_albums` 会 DELETE artists，
    但没人调用 similarity_repository.prune_missing_artists()（全仓库仅定义、零调用），
    因此 artist_similarity_feedback 会越积越多。

    用户可见后果：未配置音乐库（library_id=None）时「已忽略 N 组 · 管理」面板里
    出现打不开的死条目 "(已删除) / (已删除)"；配了库时被 JOIN 过滤掉，
    变成「看不见但删不掉的脏数据」。
    修法：在 `_cleanup_orphaned_artists_albums` 里 delete 之后调用
    `prune_missing_artists()`（或在 delete_library/delete_artist 后调用）。
    """
    from services.scan_service import _cleanup_orphaned_artists_albums

    a = ensure_artist("会消失的甲", library_id=library["id"])
    b = ensure_artist("会消失的乙", library_id=library["id"])
    sr.set_feedback(a, b, "dismissed")
    pair = sr.normalize_pair(a, b)

    # 两个艺术家都没有曲目 → 扫描的孤立记录清理会把它们删掉
    _cleanup_orphaned_artists_albums(library["id"])

    db = get_db()
    left = db.execute(
        "SELECT COUNT(*) FROM artist_similarity_feedback"
        " WHERE artist_a_id=? AND artist_b_id=?",
        pair,
    ).fetchone()[0]
    assert left == 0, "艺术家已被删除，反馈行却是孤儿（prune_missing_artists 从未接线）"


def test_confirmed_pair_does_not_vanish_silently_on_repo_error(
    client, app_ctx, library, monkeypatch
):
    """最小复现：让反馈列表查询抛错 → 接口照样 200，但 confirmed 配对凭空消失。

    api/routes.py:2417 的 `except Exception` 只写一条 warning 就继续返回 200。
    这里注入一个「仓储层故障」：贪心配对只会产出 (a,b)，(a,c) 完全依赖补充逻辑，
    补充逻辑一挂，(a,c) 就静默不见了——用户会以为「我标记的『是同一人』丢了」。

    说明：触发条件是注入的（真实 DB 故障也会走同一条路），本条是「故障时静默降级」
    的设计问题，不是在说日常路径会丢数据。
    修法：缩小 try 范围（只包住「补名字」这类可容错步骤），或至少 logger.error +
    在响应里带上 warning 字段，别让 200 掩盖真实错误。
    """
    import api.routes as routes

    a = ensure_artist("周杰伦", library_id=library["id"])
    b = ensure_artist("周杰倫", library_id=library["id"])
    c = ensure_artist("周杰倫2", library_id=library["id"])
    for x, y in ((a, b), (a, c)):
        assert (
            client.post(
                "/api/stats/similar-artists/feedback",
                json={"artist_a_id": x, "artist_b_id": y, "verdict": "confirmed"},
            ).status_code
            == 200
        )

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(routes, "list_similarity_feedback", boom)

    resp = client.get("/api/stats/similar-artists")
    assert resp.status_code == 200
    pairs = {
        tuple(sorted((g["artist_a"]["id"], g["artist_b"]["id"])))
        for g in resp.get_json()
        if g.get("feedback") == "confirmed"
    }
    missing = {tuple(sorted((a, b))), tuple(sorted((a, c)))} - pairs
    assert not missing, f"confirmed 配对静默消失（HTTP 200，只有一条 warning）: {missing}"


def test_index_html_cache_buster_bumped_for_changed_assets():
    """最小复现：git diff 改了 main.css / artist.js / artist-stats.js，index.html 却没动。

    仓库惯例（见 ce57a30 把 main.css v7→v8、files.js v5→v7）是改静态资源就 bump
    index.html 里的 `?v=`。本次 A1/A2 三个资源都改了，版本号仍是 c7/8、artist.js v3、
    artist-stats.js v2，老用户浏览器会继续用旧 JS/CSS：
    新按钮点不动、`.as-similar-actions` 等新类名没样式。
    修法：main.css→v9、artist.js→v4、artist-stats.js→v3（数字只需变大）。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    # 审查时（未 bump）的基线版本号
    baseline = {
        "css/main.css": 8,
        "js/modules/artist.js": 3,
        "js/modules/artist-stats.js": 2,
    }
    problems = []
    for asset, base in baseline.items():
        m = re.search(r"static/" + re.escape(asset) + r"\?v=(\d+)", html)
        assert m, f"index.html 里找不到 {asset} 的 ?v= 声明"
        got = int(m.group(1))
        if got <= base:
            problems.append(f"{asset}: v{got} <= 基线 v{base}")
    assert not problems, "静态资源改了但缓存版本号没 bump：" + "; ".join(problems)


def test_artist_stats_inline_onclick_uses_escjs_for_string_args():
    """最小复现：把艺术家元数据里的 name 改成  a');alert(1);//  然后点统计面板里的名字。

    artist-stats.js:440 的写法是
        onclick="searchArtistInSidebar('${esc(a.name).replace(/'/g, "\\\\'")}')"
    esc() 会把单引号转成 `&#39;`，HTML 属性解析时又被还原成裸单引号，
    于是 `\\'` 那次 replace 永远匹配不到，单引号直接闭合 JS 字符串字面量 →
    点击即可执行注入的 JS（艺术家名来自文件标签，属于外部可控输入）。

    utils.js:37 已经提供了正确助手 escJs()，repo 里其它内联 onclick 都在用它。
    修法：`onclick="searchArtistInSidebar('${escJs(a.name)}')"`（更稳的是 esc(escJs(...))，
    因为 escJs 不处理属性定界用的双引号）。
    """
    src = ARTIST_STATS_JS.read_text(encoding="utf-8")
    args = re.findall(r"searchArtistInSidebar\('\$\{(.*?)\}'\)", src)
    assert args, "没有找到 searchArtistInSidebar 的内联 onclick（写法变了？）"
    bad = [a for a in args if "escJs(" not in a]
    assert not bad, f"内联 onclick 的字符串参数没有用 escJs() 转义: {bad}"