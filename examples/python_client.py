"""临时限行核验服务调用示例（Python 标准库，无需第三方依赖）。

用法：
    python3 examples/python_client.py
可用环境变量 BASE_URL 覆盖服务地址。
"""
from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def show(title: str, obj: dict) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> None:
    show("健康检查", call("GET", "/health"))

    region = call("POST", "/api/regions", {
        "name": "A区-示例",
        "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_to": "2026-10-01T00:00:00Z",
    })
    show("登记区域", region)

    # 核验一条直线匀速轨迹：(-50,50) -> (150,50)
    # 插值交点：x=0 时为 00:00:15，x=100 时为 00:00:45
    verdict = call("POST", "/api/verify", {
        "points": [
            {"x": -50, "y": 50, "t": "2026-09-30T00:00:00Z"},
            {"x": 150, "y": 50, "t": "2026-09-30T00:01:00Z"},
        ]
    })
    show("核验结果", verdict)

    if not verdict["safe"]:
        for r in verdict["results"]:
            for c in r["crossings"]:
                print(
                    f"  ! 区域 {r['region_name']} 被穿越："
                    f"{c['entry_time']} → {c['exit_time']}"
                    f"{'（单点接触）' if c['point_contact'] else ''}"
                )

    # 安全出发时刻：固定行程（相对秒轨迹，首点 t=0）+ 候选出发窗口。
    # 行程 (-50,50)t=0 -> (150,50)t=60，对 A 区 [0,100]^2 的接触在相对 [15,45]；
    # A 区 9 月全月生效，候选窗放在生效期之前，得到一个左闭右开的安全区间。
    departures = call("POST", "/api/safe-departures", {
        "points": [
            {"x": -50, "y": 50, "t": 0},
            {"x": 150, "y": 50, "t": 60},
        ],
        "window_start": "2026-08-31T23:58:00Z",
        "window_end": "2026-08-31T23:59:30Z",
    })
    show("安全出发区间", departures)
    if departures["intervals"]:
        for iv in departures["intervals"]:
            bracket_l = "[" if iv["start_inclusive"] else "("
            bracket_r = "]" if iv["end_inclusive"] else ")"
            print(f"  可出发：{bracket_l}{iv['start']}, {iv['end']}{bracket_r}（UTC）")
    else:
        print("  候选窗口内没有安全出发时刻")

    show("删除示例区域", call("DELETE", f"/api/regions/{region['region']['id']}"))


if __name__ == "__main__":
    main()
