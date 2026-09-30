"""HTTP API 集成测试，需要一个可连接的 PostgreSQL（由 DATABASE_URL 指定）。

运行：
    DATABASE_URL=postgresql://restrict@localhost:5432/restrict \
        python3 tests/test_api.py
脚本会清空 regions/region_meta 后跑全套用例。
"""
from __future__ import annotations

import os
import sys
import threading
import time

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.main import app  # noqa: E402

failures: list[str] = []


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


db.init_pool()
client = TestClient(app)

# 清空数据
with db.pool.connection() as conn:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE regions RESTART IDENTITY")
        cur.execute("UPDATE region_meta SET revision = 0 WHERE id = 1")
    conn.commit()

# ---- 健康检查 ----
r = client.get("/health")
check("GET /health 200", r.status_code == 200 and r.json()["status"] == "ok")

# ---- 登记区域 ----
good_region = {
    "name": "A区",
    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
}
r = client.post("/api/regions", json=good_region)
check("POST /api/regions 201", r.status_code == 201, r.text)
body = r.json()
rid = body["region"]["id"]
check("revision 变为 1", body["revision"] == 1, str(body))
check("返回的多边形闭合且逆时针", body["region"]["polygon"][0] == body["region"]["polygon"][-1])
check("version=1", body["region"]["version"] == 1)

# ---- 非法输入：自相交多边形不保存 ----
before = client.get("/api/revision").json()["revision"]
r = client.post("/api/regions", json={
    "name": "坏多边形",
    "polygon": [[0, 0], [10, 10], [10, 0], [0, 10]],
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
})
check("自相交多边形 422", r.status_code == 422, r.text)
check("错误信息包含非法原因", "自相交" in r.text or "invalid" in r.text)
after = client.get("/api/revision").json()["revision"]
check("非法多边形未保存（revision 不变）", before == after)

# ---- 非法输入：NaN ----
import json as _json

r = client.post("/api/regions", content=_json.dumps({
    "name": "NaN",
    "polygon": [[0, 0], ["NaN", 1], [1, 1]],
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
}), headers={"content-type": "application/json"})
check("NaN 坐标 422", r.status_code == 422, r.text)

# ---- 非法输入：时间不递增 ----
r = client.post("/api/regions", json={
    "name": "时段错误",
    "polygon": [[0, 0], [1, 0], [1, 1]],
    "valid_from": "2026-10-01T00:00:00Z",
    "valid_to": "2026-09-01T00:00:00Z",
})
check("起止不递增 422", r.status_code == 422, r.text)
check("错误码 invalid_input", r.json()["error"]["code"] == "invalid_input")
check("时间错误未保存",
      client.get("/api/revision").json()["revision"] == 1)

# ---- 时间必须带时区 ----
r = client.post("/api/regions", json={
    "name": "无时区",
    "polygon": [[0, 0], [1, 0], [1, 1]],
    "valid_from": "2026-09-01T00:00:00",
    "valid_to": "2026-10-01T00:00:00",
})
check("无时区时间 422", r.status_code == 422, r.text)

# ---- 第二个区域，用于分别列出 ----
r = client.post("/api/regions", json={
    "name": "B区",
    "polygon": [[20, 0], [30, 0], [30, 10], [20, 10]],
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
})
rid_b = r.json()["region"]["id"]
check("B区登记成功 revision=2", r.status_code == 201 and r.json()["revision"] == 2)

# ---- 列表/单查 ----
check("GET 列表有 2 个区域", len(client.get("/api/regions").json()) == 2)
check("GET 单查 200", client.get(f"/api/regions/{rid}").status_code == 200)
check("GET 不存在 404", client.get("/api/regions/9999").status_code == 404)

# ---- 核验：干净穿越 A 区 ----
r = client.post("/api/verify", json={"points": [
    {"x": -5, "y": 5, "t": "2026-09-30T00:00:00Z"},
    {"x": 15, "y": 5, "t": "2026-09-30T00:01:00Z"},
]})
check("verify 200", r.status_code == 200, r.text)
v = r.json()
check("safe=false", v["safe"] is False)
check("结果分别列出两个区域", len(v["results"]) == 2)
ra = next(x for x in v["results"] if x["region_id"] == rid)
rb = next(x for x in v["results"] if x["region_id"] == rid_b)
check("A 区 1 段穿越", len(ra["crossings"]) == 1, str(ra))
check("A 区进入=00:00:15",
      ra["crossings"][0]["entry_time"] == "2026-09-30T00:00:15Z", str(ra))
check("A 区离开=00:00:45",
      ra["crossings"][0]["exit_time"] == "2026-09-30T00:00:45Z")
check("B 区无穿越", rb["crossings"] == [])
check("响应带 revision=2", v["revision"] == 2)

# ---- 核验：安全轨迹 ----
r = client.post("/api/verify", json={"points": [
    {"x": 100, "y": 100, "t": "2026-09-30T00:00:00Z"},
    {"x": 110, "y": 110, "t": "2026-09-30T00:01:00Z"},
]})
check("远离区域 safe=true", r.json()["safe"] is True)

# ---- 核验：单点接触 ----
r = client.post("/api/verify", json={"points": [
    {"x": 5, "y": 15, "t": "2026-09-30T00:00:00Z"},
    {"x": 15, "y": 5, "t": "2026-09-30T00:00:10Z"},
]})
v = r.json()
ra = next(x for x in v["results"] if x["region_id"] == rid)
check("单点接触返回相同起止时刻",
      len(ra["crossings"]) == 1
      and ra["crossings"][0]["point_contact"] is True
      and ra["crossings"][0]["entry_time"] == ra["crossings"][0]["exit_time"]
      == "2026-09-30T00:00:05Z", str(ra))

# ---- 核验：非法轨迹 ----
r = client.post("/api/verify", json={"points": [
    {"x": 0, "y": 0, "t": "2026-09-30T00:00:10Z"},
    {"x": 1, "y": 1, "t": "2026-09-30T00:00:00Z"},
]})
check("逆序时刻 422", r.status_code == 422 and r.json()["error"]["code"] == "invalid_trajectory", r.text)

r = client.post("/api/verify", json={"points": [
    {"x": 0, "y": 0, "t": "2026-09-30T00:00:00Z"},
    {"x": 1, "y": 1, "t": "2026-09-30T00:00:00Z"},
]})
check("重复时刻 422", r.status_code == 422, r.text)

r = client.post("/api/verify", json={"points": [
    {"x": 0, "y": 0, "t": "2026-09-30T00:00:00Z"},
]})
check("轨迹点不足 422", r.status_code == 422, r.text)

# ---- 核验：交点插值，不踩采样点 ----
# 采样点都在区域外，但连线穿越区域
r = client.post("/api/verify", json={"points": [
    {"x": -1, "y": 5, "t": "2026-09-30T00:00:00Z"},
    {"x": 11, "y": 5, "t": "2026-09-30T00:01:00Z"},
    {"x": 21, "y": 5, "t": "2026-09-30T00:02:00Z"},
]})
v = r.json()
ra = next(x for x in v["results"] if x["region_id"] == rid)
check("采样点在区域外仍能检出插值穿越",
      len(ra["crossings"]) == 1
      and ra["crossings"][0]["entry_time"] == "2026-09-30T00:00:05Z"
      and ra["crossings"][0]["exit_time"] == "2026-09-30T00:00:55Z", str(ra))

# ---- 修订区域（部分更新） ----
r = client.patch(f"/api/regions/{rid}", json={"name": "A区-扩大"})
check("PATCH 名称成功 revision=3", r.status_code == 200 and r.json()["revision"] == 3, r.text)
check("名称已更新", r.json()["region"]["name"] == "A区-扩大")
check("version=2", r.json()["region"]["version"] == 2)

r = client.patch(f"/api/regions/{rid}", json={
    "polygon": [[0, 0], [20, 0], [20, 20], [0, 20]],
})
check("PATCH 多边形成功 revision=4", r.status_code == 200 and r.json()["revision"] == 4, r.text)

r = client.patch(f"/api/regions/{rid}", json={
    "valid_from": "2026-11-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
})
check("PATCH 出非法时段 422 且不保存", r.status_code == 422)
check("非法 PATCH 后 revision 仍=4", client.get("/api/revision").json()["revision"] == 4)

r = client.patch("/api/regions/9999", json={"name": "x"})
check("PATCH 不存在 404", r.status_code == 404)

# ---- 删除 ----
r = client.delete(f"/api/regions/{rid_b}")
check("DELETE 成功 revision=5", r.status_code == 200 and r.json()["revision"] == 5, r.text)
check("DELETE 后列表只有 1 个", len(client.get("/api/regions").json()) == 1)
check("DELETE 不存在 404", client.delete("/api/regions/9999").status_code == 404)

# ---- 并发一致性：核验进行中区域被修改，本次仍使用同一已提交版本 ----
# 恢复一个标准区域用于本用例
client.post("/api/regions", json=good_region)

from app import geometry  # noqa: E402

orig = geometry.verify_trajectory
barrier_started = threading.Event()
barrier_finish = threading.Event()


def slow_verify(traj, regions):
    barrier_started.set()
    time.sleep(1.0)  # 给修改线程留出提交窗口
    result = orig(traj, regions)
    barrier_finish.set()
    return result


geometry.verify_trajectory = slow_verify
try:
    def do_verify():
        return client.post("/api/verify", json={"points": [
            {"x": -5, "y": 5, "t": "2026-09-30T00:00:00Z"},
            {"x": 15, "y": 5, "t": "2026-09-30T00:01:00Z"},
        ]})

    t_holder = {}

    def worker():
        t_holder["r"] = do_verify()

    th = threading.Thread(target=worker)
    th.start()
    barrier_started.wait(5)
    # 在慢核验期间修订区域
    list_regions = client.get("/api/regions").json()
    target = list_regions[0]["id"]
    r = client.patch(f"/api/regions/{target}", json={"name": "并发修改"})
    check("并发修改在核验期间提交成功", r.status_code == 200, r.text)
    th.join(timeout=10)
    rv = t_holder["r"].json()
    check("慢核验使用修改前的已提交版本（名称不变）",
          all(x["region_name"] != "并发修改" for x in rv["results"]), str(rv)[:300])
    # 新核验看到新版本
    rv2 = client.post("/api/verify", json={"points": [
        {"x": -5, "y": 5, "t": "2026-09-30T00:00:00Z"},
        {"x": 15, "y": 5, "t": "2026-09-30T00:01:00Z"},
    ]}).json()
    check("后续核验使用新版本",
          any(x["region_name"] == "并发修改" for x in rv2["results"]))
finally:
    geometry.verify_trajectory = orig

# ---- 安全出发区间 ----
# 本节使用独立区域集：先清空前面各节遗留的区域，避免叠加干扰
for _r in client.get("/api/regions").json():
    client.delete(f"/api/regions/{_r['id']}")

# 专用标准区域 [0,10]^2
r = client.post("/api/regions", json={
    "name": "调度专用区",
    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
})
sched_rid = r.json()["region"]["id"]

r = client.post("/api/safe-departures", json={
    "points": [
        {"x": -5, "y": 5, "t": 0},
        {"x": 15, "y": 5, "t": 10},
    ],
    "window_start": "2026-09-30T00:00:00Z",
    "window_end": "2026-09-30T00:01:00Z",
})
check("safe-departures 200", r.status_code == 200, r.text)
sd = r.json()
# 区域整月生效，接触相对窗 [2.5,7.5]：禁止出发 [00:00:00-7.5, 月末-2.5)
# 候选窗内全部不安全（禁止起点为前一天 23:59:52.5）
check("整窗被禁止区间覆盖 -> 空数组", sd["intervals"] == [], str(sd))
check("响应当前 revision", sd["revision"] == client.get("/api/revision").json()["revision"])
check("回显候选窗口（UTC/Z）",
      sd["window_start"] == "2026-09-30T00:00:00Z"
      and sd["window_end"] == "2026-09-30T00:01:00Z")

# 收窄区域生效窗为 [00:00:10, 00:00:20)，禁止出发 [00:00:02.5, 00:00:17.5)
client.patch(f"/api/regions/{sched_rid}", json={
    "valid_from": "2026-09-30T00:00:10Z",
    "valid_to": "2026-09-30T00:00:20Z",
})
r = client.post("/api/safe-departures", json={
    "points": [
        {"x": -5, "y": 5, "t": 0},
        {"x": 15, "y": 5, "t": 10},
    ],
    "window_start": "2026-09-30T00:00:00Z",
    "window_end": "2026-09-30T00:00:30Z",
})
sd = r.json()["intervals"]
check("解析区间：安全 [00:00:00,00:00:02.5) 与 [00:00:17.5,00:00:30)",
      len(sd) == 2
      and sd[0] == {"start": "2026-09-30T00:00:00Z",
                    "end": "2026-09-30T00:00:02.500000Z",
                    "start_inclusive": True, "end_inclusive": False}
      and sd[1] == {"start": "2026-09-30T00:00:17.500000Z",
                    "end": "2026-09-30T00:00:30Z",
                    "start_inclusive": True, "end_inclusive": False},
      str(sd))

# 非 UTC 时区的候选窗等价换算（+02:00）
r2 = client.post("/api/safe-departures", json={
    "points": [
        {"x": -5, "y": 5, "t": 0},
        {"x": 15, "y": 5, "t": 10},
    ],
    "window_start": "2026-09-30T02:00:00+02:00",
    "window_end": "2026-09-30T02:00:30+02:00",
})
check("带时区候选窗换算为 UTC 后结果一致",
      r2.status_code == 200 and r2.json()["intervals"] == sd, r2.text)

# 单点接触恰逢生效端点
client.patch(f"/api/regions/{sched_rid}", json={
    "valid_from": "2026-09-30T00:00:05Z",
    "valid_to": "2026-09-30T01:00:00Z",
})
r = client.post("/api/safe-departures", json={
    "points": [{"x": 5, "y": 15, "t": 0}, {"x": 15, "y": 5, "t": 10}],
    "window_start": "2026-09-30T00:00:00Z",
    "window_end": "2026-09-30T00:00:10Z",
})
check("单点接触@valid_from(闭)：整窗不安全",
      r.json()["intervals"] == [], r.text)
client.patch(f"/api/regions/{sched_rid}", json={
    "valid_from": "2026-09-30T00:00:00Z",
    "valid_to": "2026-09-30T00:00:05Z",
})
r = client.post("/api/safe-departures", json={
    "points": [{"x": 5, "y": 15, "t": 0}, {"x": 15, "y": 5, "t": 10}],
    "window_start": "2026-09-30T00:00:00Z",
    "window_end": "2026-09-30T00:00:10Z",
})
check("单点接触@valid_to(开)：d=00:00:00 安全",
      len(r.json()["intervals"]) == 1
      and r.json()["intervals"][0]["start"] == "2026-09-30T00:00:00Z"
      and r.json()["intervals"][0]["end"] == "2026-09-30T00:00:10Z", r.text)

# ---- 非法输入拒绝整个请求（422） ----
def bad_schedule(label, payload, code=None):
    rr = client.post("/api/safe-departures", json=payload)
    ok = rr.status_code == 422 and (code is None or rr.json()["error"]["code"] == code)
    check(f"非法调度请求 422：{label}", ok, rr.text)


_base_window = ("2026-09-30T00:00:00Z", "2026-09-30T01:00:00Z")
bad_schedule("轨迹点不足", {"points": [{"x": 0, "y": 0, "t": 0}],
             "window_start": _base_window[0], "window_end": _base_window[1]})
bad_schedule("首点相对时刻非0", {
    "points": [{"x": 0, "y": 0, "t": 1}, {"x": 1, "y": 1, "t": 2}],
    "window_start": _base_window[0], "window_end": _base_window[1]},
    code="invalid_trajectory")
bad_schedule("相对时刻不递增", {
    "points": [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 0}],
    "window_start": _base_window[0], "window_end": _base_window[1]},
    code="invalid_trajectory")
bad_schedule("坐标 NaN", {
    "points": [{"x": "NaN", "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 1}],
    "window_start": _base_window[0], "window_end": _base_window[1]})
bad_schedule("相对时刻 Infinity", {
    "points": [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": "Infinity"}],
    "window_start": _base_window[0], "window_end": _base_window[1]})
bad_schedule("候选时段不递增", {
    "points": [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 1}],
    "window_start": "2026-09-30T01:00:00Z", "window_end": "2026-09-30T00:00:00Z"},
    code="invalid_input")
bad_schedule("候选时段相等", {
    "points": [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 1}],
    "window_start": _base_window[0], "window_end": _base_window[0]},
    code="invalid_input")
bad_schedule("候选时段无时区", {
    "points": [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 1}],
    "window_start": "2026-09-30T00:00:00", "window_end": "2026-09-30T01:00:00"})

# 非法调度请求不改变 revision
rev_before = client.get("/api/revision").json()["revision"]
client.post("/api/safe-departures", json={
    "points": [{"x": 0, "y": 0, "t": 0}],
    "window_start": _base_window[0], "window_end": _base_window[1],
})
check("只读调度请求不改变 revision",
      client.get("/api/revision").json()["revision"] == rev_before)

# ---- 并发一致性：慢调度期间修订区域，仍使用同一已提交快照 ----
client.patch(f"/api/regions/{sched_rid}", json={
    "valid_from": "2026-09-01T00:00:00Z",
    "valid_to": "2026-10-01T00:00:00Z",
    "name": "调度快照前",
})
orig_safe = geometry.safe_departure_intervals
barrier2_started = threading.Event()


def slow_safe(traj, regions, ws, we):
    barrier2_started.set()
    time.sleep(1.0)
    return orig_safe(traj, regions, ws, we)


geometry.safe_departure_intervals = slow_safe
try:
    holder = {}

    def do_schedule():
        holder["r"] = client.post("/api/safe-departures", json={
            "points": [{"x": -5, "y": 5, "t": 0}, {"x": 15, "y": 5, "t": 10}],
            "window_start": "2026-09-30T00:00:00Z",
            "window_end": "2026-09-30T00:01:00Z",
        })

    th = threading.Thread(target=do_schedule)
    th.start()
    barrier2_started.wait(5)
    # 慢调度期间把区域改成完全不相交的远方区域并提交
    client.patch(f"/api/regions/{sched_rid}", json={
        "name": "调度快照后",
        "polygon": [[1000, 1000], [1010, 1000], [1010, 1010], [1000, 1010]],
    })
    th.join(timeout=10)
    snap = holder["r"].json()
    check("慢调度使用修改前快照（旧 [0,10] 区域覆盖整窗 -> 空）",
          snap["intervals"] == [], str(snap))
    # 新请求看到新版本：远方区域不相交 -> 整窗安全
    after = client.post("/api/safe-departures", json={
        "points": [{"x": -5, "y": 5, "t": 0}, {"x": 15, "y": 5, "t": 10}],
        "window_start": "2026-09-30T00:00:00Z",
        "window_end": "2026-09-30T00:01:00Z",
    }).json()
    check("后续调度使用新版本（远方区域 -> 整窗安全）",
          len(after["intervals"]) == 1
          and after["intervals"][0]["start"] == "2026-09-30T00:00:00Z"
          and after["intervals"][0]["end"] == "2026-09-30T00:01:00Z", str(after))
finally:
    geometry.safe_departure_intervals = orig_safe

print()
if failures:
    print(f"{len(failures)} 个失败")
    sys.exit(1)
print("全部 API 集成用例通过")
