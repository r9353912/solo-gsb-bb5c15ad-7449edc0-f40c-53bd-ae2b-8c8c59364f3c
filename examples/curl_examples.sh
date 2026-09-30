#!/usr/bin/env bash
# 临时限行核验服务调用示例（curl）。
# 用法：BASE_URL=http://localhost:8000 bash examples/curl_examples.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

echo "== 1. 健康检查 =="
curl -fsS "$BASE_URL/health"; echo

echo "== 2. 登记限行区域 A：正方形 [0,0]-[100,100]，9 月全月生效 =="
A_ID=$(curl -fsS -X POST "$BASE_URL/api/regions" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "A区-焊接作业面",
        "polygon": [[0,0],[100,0],[100,100],[0,100]],
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_to":   "2026-10-01T00:00:00Z"
      }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["region"]["id"])')
echo "A 区 id=$A_ID"

echo "== 3. 登记限行区域 B：右侧 200~300 的带状区域 =="
curl -fsS -X POST "$BASE_URL/api/regions" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "B区-吊装半径",
        "polygon": [[200,0],[300,0],[300,100],[200,100]],
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_to":   "2026-10-01T00:00:00Z"
      }'; echo

echo "== 4. 查询当前区域集合版本号 =="
curl -fsS "$BASE_URL/api/revision"; echo

echo "== 5. 列出全部区域 =="
curl -fsS "$BASE_URL/api/regions"; echo

echo "== 6. 核验轨迹：3 个点，直线匀速，穿过 A 区但不碰 B 区 =="
# 09-30 00:00:00 位于 (-50,50)；1 分钟后到 (150,50)；2 分钟后到 (350,50)
# A 区穿越由线段插值得到：进入 00:00:15（x=0），离开 00:00:45（x=100）
curl -fsS -X POST "$BASE_URL/api/verify" \
  -H 'Content-Type: application/json' \
  -d '{
        "points": [
          {"x": -50, "y": 50, "t": "2026-09-30T00:00:00Z"},
          {"x": 150, "y": 50, "t": "2026-09-30T00:01:00Z"},
          {"x": 350, "y": 50, "t": "2026-09-30T00:02:00Z"}
        ]
      }'; echo

echo "== 7. 核验轨迹：斜向擦过 A 区右上角 (100,100)，应为单点接触 =="
curl -fsS -X POST "$BASE_URL/api/verify" \
  -H 'Content-Type: application/json' \
  -d '{
        "points": [
          {"x":  50, "y": 150, "t": "2026-09-30T00:00:00Z"},
          {"x": 150, "y":  50, "t": "2026-09-30T00:00:10Z"}
        ]
      }'; echo

echo "== 8. 修订区域 A（改名 + 扩大到 120x120） =="
curl -fsS -X PATCH "$BASE_URL/api/regions/$A_ID" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "A区-扩大后",
        "polygon": [[0,0],[120,0],[120,120],[0,120]]
      }'; echo

echo "== 9. 删除区域 B（动态取 id） =="
B_ID=$(curl -fsS "$BASE_URL/api/regions" \
  | python3 -c 'import sys,json; print(next(r["id"] for r in json.load(sys.stdin) if r["name"]=="B区-吊装半径"))')
curl -fsS -X DELETE "$BASE_URL/api/regions/$B_ID"; echo

echo "完成。"
