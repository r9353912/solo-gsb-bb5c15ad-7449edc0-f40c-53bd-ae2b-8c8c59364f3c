"""geometry 引擎的离线验证（不依赖数据库）。

直接 `python3 tests/test_geometry.py` 运行；失败时断言退出码非 0。
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import Polygon

from app import geometry


def E(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def T(iso: str, x: float, y: float):
    return (E(iso), x, y)


def region(rid, name, vf, vt, ring):
    return {
        "id": rid,
        "name": name,
        "valid_from": E(vf),
        "valid_to": E(vt),
        "polygon": Polygon(ring),
    }


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


# 正方形区域 [0,10]x[0,10]，整时段生效
SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10]]
R = region(1, "S", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z", SQUARE)

# 1) 干净穿越：从 (-5,5) 到 (15,5)，x=0 进入（00:00:15），x=10 离开（00:00:45）
traj = [
    T("2026-09-30T00:00:00Z", -5, 5),
    T("2026-09-30T00:01:00Z", 15, 5),
]
res = geometry.verify_trajectory(traj, [R])
cs = res[0]["crossings"]
check("干净穿越只有 1 段", len(cs) == 1)
if cs:
    check("进入时刻插值=00:00:15", approx(cs[0]["entry_time"], E("2026-09-30T00:00:15Z")))
    check("离开时刻插值=00:00:45", approx(cs[0]["exit_time"], E("2026-09-30T00:00:45Z")))
    check("非点接触", cs[0]["point_contact"] is False)

# 2) 沿边界运动算穿越（从 (0,0) 沿底边到 (10,0)）
traj = [T("2026-09-30T00:00:00Z", 0, 0), T("2026-09-30T00:00:10Z", 10, 0)]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("沿边界运动算穿越，1 段，覆盖全程",
      len(cs) == 1 and approx(cs[0]["entry_time"], E("2026-09-30T00:00:00Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:00:10Z")))

# 3) 单点接触：直线擦过角点 (10,10)
traj = [T("2026-09-30T00:00:00Z", 5, 15), T("2026-09-30T00:00:10Z", 15, 5)]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("单点接触角点：1 个退化区间", len(cs) == 1)
if cs:
    check("起止时刻相同=00:00:05",
          approx(cs[0]["entry_time"], E("2026-09-30T00:00:05Z"))
          and approx(cs[0]["exit_time"], E("2026-09-30T00:00:05Z"))
          and cs[0]["point_contact"] is True)

# 4) 擦边：轨迹与顶边重合一段
traj = [T("2026-09-30T00:00:00Z", -5, 10), T("2026-09-30T00:00:10Z", 15, 10)]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("沿边直线：00:00:02.5 至 00:00:07.5",
      len(cs) == 1
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:02.5Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:00:07.5Z")))

# 5) 左闭右开生效窗口：区域 [00:00:30, 00:01:30)，轨迹全程在区域内
R2 = region(2, "W", "2026-09-30T00:00:30Z", "2026-09-30T00:01:30Z", SQUARE)
traj = [
    T("2026-09-30T00:00:00Z", 5, 5),
    T("2026-09-30T00:02:00Z", 5, 5),
]
cs = geometry.verify_trajectory(traj, [R2])[0]["crossings"]
check("窗口裁剪：起=00:00:30（闭）止=00:01:30（开）",
      len(cs) == 1
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:30Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:01:30Z"))
      and cs[0]["entry_inclusive"] is True
      and cs[0]["exit_inclusive"] is False)

# 6) 接触时刻恰好等于 valid_to（窗口右端点开）：不记录
R3 = region(3, "W3", "2026-09-30T00:00:00Z", "2026-09-30T00:00:30Z", SQUARE)
traj = [T("2026-09-30T00:00:00Z", 5, 15), T("2026-09-30T00:01:00Z", 15, 5)]
# 接触角点 (10,10) 的时刻为 00:00:30 == valid_to，区域已失效
cs = geometry.verify_trajectory(traj, [R3])[0]["crossings"]
check("valid_to 时刻的单点接触不记录", len(cs) == 0)

# 6b) 接触发生在 valid_to 之后 → 不记录
R4 = region(4, "W4", "2026-09-30T00:00:00Z", "2026-09-30T00:00:20Z", SQUARE)
cs = geometry.verify_trajectory(traj, [R4])[0]["crossings"]
check("valid_to 之后才接触：不记录", len(cs) == 0)

# 6c) 接触时刻恰好等于 valid_from（左端闭）：记录为单点接触
R5 = region(5, "W5", "2026-09-30T00:00:05Z", "2026-09-30T00:01:00Z", SQUARE)
traj5 = [T("2026-09-30T00:00:00Z", 5, 15), T("2026-09-30T00:00:10Z", 15, 5)]
cs = geometry.verify_trajectory(traj5, [R5])[0]["crossings"]
check("valid_from 时刻的单点接触记录（左端闭）",
      len(cs) == 1 and cs[0]["point_contact"] is True
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:05Z"))
      and cs[0]["exit_inclusive"] is True)

# 6d) 正长度穿越在 valid_to 处被截断：保留 [.., valid_to) 且 exit_inclusive=False
R6 = region(6, "W6", "2026-09-30T00:00:20Z", "2026-09-30T00:00:40Z", SQUARE)
traj6 = [T("2026-09-30T00:00:00Z", 5, 5), T("2026-09-30T00:01:00Z", 5, 5)]
cs = geometry.verify_trajectory(traj6, [R6])[0]["crossings"]
check("穿越被 [vf,vt) 窗口裁剪：起闭止开",
      len(cs) == 1
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:20Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:00:40Z"))
      and cs[0]["entry_inclusive"] is True
      and cs[0]["exit_inclusive"] is False)

# 7) 段间接续合并：两段分别进入区域、在边界点相接
traj = [
    T("2026-09-30T00:00:00Z", -5, 5),   # 00:00:15 进入
    T("2026-09-30T00:00:30Z", 10, 5),   # 00:00:30 到达右边界
    T("2026-09-30T00:01:00Z", 25, 5),   # 00:00:30 从边界继续向外（相接）
]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("相邻段在边界相接：合并为 1 段",
      len(cs) == 1
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:10Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:00:30Z")))

# 8) 离开后再次进入 → 两段
traj = [
    T("2026-09-30T00:00:00Z", -5, 5),
    T("2026-09-30T00:00:20Z", 5, 5),    # 区域内
    T("2026-09-30T00:00:40Z", 25, 5),   # 已离开
    T("2026-09-30T00:01:00Z", 5, 5),    # 回到区域内
    T("2026-09-30T00:01:20Z", -5, 5),
]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("离开后再进入：2 段", len(cs) == 2)

# 9) 两区域分别列出
Rb = region(2, "B", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z",
            [[20, 0], [30, 0], [30, 10], [20, 10]])
traj = [T("2026-09-30T00:00:00Z", -5, 5), T("2026-09-30T00:01:00Z", 40, 5)]
res = geometry.verify_trajectory(traj, [R, Rb])
check("不同区域分别列出", len(res) == 2 and all(len(r["crossings"]) == 1 for r in res))

# 10) 完全不相交
traj = [T("2026-09-30T00:00:00Z", 50, 50), T("2026-09-30T00:01:00Z", 60, 60)]
check("不相交：无穿越", geometry.verify_trajectory(traj, [R])[0]["crossings"] == [])

# 11) 起止都在内部
traj = [T("2026-09-30T00:00:00Z", 2, 2), T("2026-09-30T00:00:10Z", 8, 8)]
cs = geometry.verify_trajectory(traj, [R])[0]["crossings"]
check("整段在内部：覆盖全程", len(cs) == 1
      and approx(cs[0]["entry_time"], E("2026-09-30T00:00:00Z"))
      and approx(cs[0]["exit_time"], E("2026-09-30T00:00:10Z")))

# ---------------------------------------------------------------- 输入校验
def expect_error(label, fn):
    try:
        fn()
    except (geometry.RegionError, geometry.TrajectoryError) as e:
        check(f"{label}（{e}）", True)
        return
    check(label + "：应当报错", False)


expect_error("自相交多边形", lambda: geometry.validate_polygon(
    [[0, 0], [10, 10], [10, 0], [0, 10]]))
expect_error("顶点不足", lambda: geometry.validate_polygon([[0, 0], [1, 1]]))
expect_error("NaN 坐标", lambda: geometry.validate_polygon(
    [[0, 0], [float("nan"), 1], [1, 1]]))
expect_error("Inf 坐标", lambda: geometry.validate_polygon(
    [[0, 0], [float("inf"), 1], [1, 1]]))
expect_error("相邻重复顶点", lambda: geometry.validate_polygon(
    [[0, 0], [0, 0], [1, 1], [2, 2]]))

# 显式闭合环是允许的
try:
    ring = geometry.validate_polygon([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    check("显式闭合环可接受", len(ring) == 5 and ring[0] == ring[-1])
except geometry.RegionError as e:
    check(f"显式闭合环可接受（异常: {e}）", False)

expect_error("轨迹点不足", lambda: geometry.validate_trajectory(
    [T("2026-09-30T00:00:00Z", 0, 0)], lambda p: datetime.fromtimestamp(p[0], timezone.utc)))

class P:
    def __init__(self, iso, x, y):
        self.x, self.y, self.t = x, y, datetime.fromisoformat(iso.replace("Z", "+00:00"))

expect_error("重复轨迹时刻", lambda: geometry.validate_trajectory(
    [P("2026-09-30T00:00:00Z", 0, 0), P("2026-09-30T00:00:00Z", 1, 1)],
    lambda p: p.t))
expect_error("逆序轨迹时刻", lambda: geometry.validate_trajectory(
    [P("2026-09-30T00:00:10Z", 0, 0), P("2026-09-30T00:00:00Z", 1, 1)],
    lambda p: p.t))
expect_error("轨迹 NaN 坐标", lambda: geometry.validate_trajectory(
    [P("2026-09-30T00:00:00Z", float("nan"), 0), P("2026-09-30T00:00:10Z", 1, 1)],
    lambda p: p.t))

# 零长度段（坐标相同、时间不同）合法：停留在原地
cs = geometry.verify_trajectory(
    [T("2026-09-30T00:00:00Z", 5, 5), T("2026-09-30T00:00:10Z", 5, 5)], [R]
)[0]["crossings"]
check("零长度段停留在区域内：1 段", len(cs) == 1)

# ---------------------------------------------------------------- 安全出发时刻

def RP(x, y, t):
    """相对时间轨迹点。"""
    p = type("P", (), {})()
    p.x, p.y, p.t = x, y, t
    return p


# (-5,5)t=0 -> (15,5)t=10：几何接触相对时间闭区间 [2.5, 7.5]
rel_traj = geometry.validate_relative_trajectory([RP(-5, 5, 0), RP(15, 5, 10)])
check("相对轨迹校验通过", rel_traj == [(0.0, -5.0, 5.0), (10.0, 15.0, 5.0)])
contacts = geometry.geometric_contact_intervals(rel_traj, R["polygon"])
check("几何接触相对区间 [2.5, 7.5]", len(contacts) == 1
      and approx(contacts[0][0], 2.5) and approx(contacts[0][1], 7.5))

# 区域 10:00~11:00 生效；候选窗 09:59:50~10:00:30
# 禁止出发区间 [ws-7.5, we-2.5) = [09:59:52.5, 10:59:57.5)
# 窗内安全仅 [09:59:50, 09:59:52.5)
RS = region(7, "S", "2026-09-30T10:00:00Z", "2026-09-30T11:00:00Z", SQUARE)
safe = geometry.safe_departure_intervals(
    rel_traj, [RS], E("2026-09-30T09:59:50Z"), E("2026-09-30T10:00:30Z"))
check("基本安全区间 [09:59:50, 09:59:52.5)",
      len(safe) == 1 and approx(safe[0][0], E("2026-09-30T09:59:50Z"))
      and approx(safe[0][1], E("2026-09-30T09:59:52.5Z")))

# 候选窗完全落在禁止区间内 -> 空数组
safe = geometry.safe_departure_intervals(
    rel_traj, [RS], E("2026-09-30T10:10:00Z"), E("2026-09-30T10:20:00Z"))
check("无安全出发时刻返回空数组", safe == [])

# 无区域 -> 整个候选窗都安全
safe = geometry.safe_departure_intervals(
    rel_traj, [], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:30:00Z"))
check("无区域时整窗安全", len(safe) == 1
      and approx(safe[0][0], E("2026-09-30T10:00:00Z"))
      and approx(safe[0][1], E("2026-09-30T10:30:00Z")))

# 两区域各切掉一段，中间留安全缝
RS1 = region(8, "S1", "2026-09-30T10:00:00Z", "2026-09-30T10:00:05Z", SQUARE)
RS2 = region(9, "S2", "2026-09-30T10:00:15Z", "2026-09-30T10:00:25Z", SQUARE)
# 禁1=[ws-7.5, we-2.5)=[09:59:57.5, 10:00:02.5)
# 禁2=[10:00:07.5, 10:00:22.5)
safe = geometry.safe_departure_intervals(
    rel_traj, [RS1, RS2], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:00:15Z"))
check("两段禁止区间间留安全缝 [10:00:02.5, 10:00:07.5)",
      len(safe) == 1
      and approx(safe[0][0], E("2026-09-30T10:00:02.5Z"))
      and approx(safe[0][1], E("2026-09-30T10:00:07.5Z")))

# 半开相接：禁1右端 10:00:02.5 与禁2左端 10:00:02.5 相接 -> 合并，安全缝消失
RS3 = region(10, "S3", "2026-09-30T10:00:07.5Z", "2026-09-30T10:00:15Z", SQUARE)
# 禁3 起点 = 10:00:07.5 - 7.5 = 10:00:00
RS4 = region(11, "S4", "2026-09-30T10:00:10Z", "2026-09-30T10:00:20Z", SQUARE)
# 禁3=[10:00:00, 10:00:12.5)，禁4=[10:00:02.5, 10:00:17.5)：重叠覆盖整窗
safe = geometry.safe_departure_intervals(
    rel_traj, [RS3, RS4], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:00:10Z"))
check("重叠禁止区间取并后整窗不安全", safe == [])

# 单点接触（相对 t=5 擦角点）+ 恰逢 valid_from（闭）：d 使接触落在 valid_from 被禁
rel_touch = geometry.validate_relative_trajectory([RP(5, 15, 0), RP(15, 5, 10)])
RVF = region(12, "VF", "2026-09-30T10:00:05Z", "2026-09-30T11:00:00Z", SQUARE)
# 禁=[10:00:05-5, 11:00-5)=[10:00:00, 10:59:55)；d=10:00:00 不安全
safe = geometry.safe_departure_intervals(
    rel_touch, [RVF], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:00:10Z"))
check("单点接触恰逢 valid_from（闭）：该出发时刻被禁", safe == [])
# 候选窗从 10:59:55 起：d=10:59:55 时接触==valid_to（开）安全
safe = geometry.safe_departure_intervals(
    rel_touch, [RVF], E("2026-09-30T10:59:55Z"), E("2026-09-30T11:00:05Z"))
check("接触在 valid_to（开）时刻安全：[10:59:55, 11:00:05)",
      len(safe) == 1
      and approx(safe[0][0], E("2026-09-30T10:59:55Z"))
      and approx(safe[0][1], E("2026-09-30T11:00:05Z")))

# 单点接触恰逢 valid_to（开）：禁止区间 [.., valid_to-5) 在其右端点开
RVT = region(13, "VT", "2026-09-30T10:00:00Z", "2026-09-30T10:00:05Z", SQUARE)
# 禁=[09:59:55, 10:00:00)；候选窗 [10:00:00, ...) 全部安全（d=10:00:00 接触==valid_to 开）
safe = geometry.safe_departure_intervals(
    rel_touch, [RVT], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:00:10Z"))
check("单点接触恰逢 valid_to（开）：d=valid_to-接触偏移 安全",
      len(safe) == 1
      and approx(safe[0][0], E("2026-09-30T10:00:00Z"))
      and approx(safe[0][1], E("2026-09-30T10:00:10Z")))

# 沿边界运动（接触覆盖整个相对时段 [0,10]）：禁=[ws-10, we)
rel_edge = geometry.validate_relative_trajectory([RP(0, 0, 0), RP(10, 0, 10)])
safe = geometry.safe_departure_intervals(
    rel_edge, [RS], E("2026-09-30T09:59:00Z"), E("2026-09-30T11:30:00Z"))
check("沿边界运动：禁 [09:59:50, 11:00:00)，两侧安全",
      len(safe) == 2
      and approx(safe[0][0], E("2026-09-30T09:59:00Z"))
      and approx(safe[0][1], E("2026-09-30T09:59:50Z"))
      and approx(safe[1][0], E("2026-09-30T11:00:00Z"))
      and approx(safe[1][1], E("2026-09-30T11:30:00Z")))

# 多段轨迹与多个区域的多段安全区间：禁止区间内外交替
# 用不生效（窗口远在未来）的区域应等价于无区域
RDEAD = region(14, "DEAD", "2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z", SQUARE)
safe = geometry.safe_departure_intervals(
    rel_traj, [RDEAD], E("2026-09-30T10:00:00Z"), E("2026-09-30T10:30:00Z"))
check("区域在候选范围外不生效：整窗安全", len(safe) == 1)

# 相对轨迹校验
expect_error("相对轨迹首点非0", lambda: geometry.validate_relative_trajectory(
    [RP(0, 0, 1), RP(1, 1, 2)]))
expect_error("相对轨迹时刻重复", lambda: geometry.validate_relative_trajectory(
    [RP(0, 0, 0), RP(1, 1, 0)]))
expect_error("相对轨迹时刻逆序", lambda: geometry.validate_relative_trajectory(
    [RP(0, 0, 0), RP(1, 1, -1)]))
expect_error("相对轨迹点不足", lambda: geometry.validate_relative_trajectory(
    [RP(0, 0, 0)]))
expect_error("相对轨迹 NaN 坐标", lambda: geometry.validate_relative_trajectory(
    [RP(float("nan"), 0, 0), RP(1, 1, 1)]))
expect_error("相对轨迹 Inf 时刻", lambda: geometry.validate_relative_trajectory(
    [RP(0, 0, 0), RP(1, 1, float("inf"))]))

print()
if failures:
    print(f"{len(failures)} 个用例失败")
    sys.exit(1)
print("全部几何用例通过")
