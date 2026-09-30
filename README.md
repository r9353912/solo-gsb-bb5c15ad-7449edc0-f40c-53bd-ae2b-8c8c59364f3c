# 临时限行核验服务

面向场地安全员的临时限行区域登记与轨迹穿越核验服务。安全员在平面直角坐标系中
登记带 UTC 生效时段的简单多边形区域；系统对按时刻严格递增、相邻点直线匀速运动的
连续轨迹做整段核验，返回每个区域的穿越起止时刻以及总体是否安全。

此外提供**场地调度**能力：给定一条以"相对秒数"描述的固定行程和带时区的候选出发
时段 `[window_start, window_end)`，精确计算所有平移到 UTC 后全程不触碰任何生效
限行区的安全出发区间。

## 核验规则

* **轨迹**：至少 2 个点 `(x, y, t)`；时刻必须严格递增（重复/逆序报错且不处理）；
  相邻点之间按**直线匀速**运动。
* **交点插值**：交点时刻通过线段参数插值得到，**不做采样点检查**——即使所有提交
  的采样点都在区域外，只要两点连线穿过区域就会被检出。
* **边界即穿越**：沿边界运动、与顶点单点接触都算穿越；单点接触返回
  `entry_time == exit_time` 的退化区间，并置 `point_contact: true`。
* **区间合并**：同一区域相接（端点相同）或重叠的穿越段合并为一段；
  不同区域分别列出。
* **生效时段左闭右开**：区域仅在 `[valid_from, valid_to)` 内参与核验。
  * 接触时刻恰为 `valid_from`：参与（左端闭）；
  * 接触时刻恰为 `valid_to`：不参与（右端开，此时区域已失效）；
  * 正长度穿越跨过 `valid_to` 时，区间在 `valid_to` 处截断，
    `exit_inclusive=false`。
* **非法输入不保存**：非法多边形（自相交、退化、面积为 0）、非有限坐标
  （NaN/Infinity）、`valid_from >= valid_to` 均返回 422 且不落库。
* **一致性快照**：一次核验在单个 `REPEATABLE READ READ ONLY` 事务中读取区域集合，
  即使核验期间有区域被并发登记/修订/删除，本次核验仍使用同一已提交版本；
  响应中的 `revision` 标明所用集合版本。

## 技术栈

Python 3.11 · FastAPI · PostgreSQL 16 · Shapely 2（GEOS）· psycopg3 · Uvicorn

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000 | 服务根路径（端点列表） |
| http://localhost:8000/docs | Swagger UI（可在线调用） |
| http://localhost:8000/openapi.json | OpenAPI 描述 |
| http://localhost:8000/health | 健康检查 |
| PostgreSQL | localhost:5432（用户/库/密码均为 restrict） |

首次启动时应用会自动建表（`regions`、`region_meta`）并初始化全局版本号，
无需手工执行 SQL。数据库未就绪时应用会自动重试连接（默认 30 次、间隔 1 秒）。

停止并清理数据卷：

```bash
docker compose down -v
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://restrict:restrict@localhost:5432/restrict` | PostgreSQL DSN |
| `API_PORT` | `8000` | Compose 映射到宿主机的 API 端口 |
| `POSTGRES_PORT` | `5432` | Compose 映射到宿主机的数据库端口 |
| `DB_CONNECT_RETRIES` | `30` | 启动时连接数据库的重试次数 |
| `DB_CONNECT_INTERVAL` | `1.0` | 重试间隔（秒） |

例如改用 18000 端口：`API_PORT=18000 docker compose up --build`。

## 本地（无 Docker）运行

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://restrict:restrict@localhost:5432/restrict"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## HTTP API

时间字段一律使用带时区的 ISO8601，建议统一 `...Z`（UTC）。
多边形顶点按 `[[x, y], ...]` 提交，首尾点可显式闭合也可不闭合；
服务端校验为简单多边形后归一化为逆时针闭合环存储。

### 区域管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/regions` | 登记区域（201） |
| GET | `/api/regions` | 列出全部区域 |
| GET | `/api/regions/{id}` | 查询单个区域 |
| PATCH | `/api/regions/{id}` | 修订区域（字段任意组合，部分更新）；`version` +1 |
| DELETE | `/api/regions/{id}` | 删除区域 |
| GET | `/api/revision` | 当前区域集合全局版本号（任意写操作后单调 +1） |

### 轨迹与调度

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/verify` | 绝对时刻轨迹的整段穿越核验 |
| POST | `/api/safe-departures` | 固定行程（相对秒轨迹）在候选窗口内的所有安全出发区间 |

登记示例：

```bash
curl -X POST http://localhost:8000/api/regions \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "A区-焊接作业面",
        "polygon": [[0,0],[100,0],[100,100],[0,100]],
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_to":   "2026-10-01T00:00:00Z"
      }'
```

### 轨迹核验 `POST /api/verify`

请求：

```json
{
  "points": [
    {"x": -50, "y": 50, "t": "2026-09-30T00:00:00Z"},
    {"x": 150, "y": 50, "t": "2026-09-30T00:01:00Z"}
  ]
}
```

响应（该轨迹在 00:00:15 进入、00:00:45 离开 0~100 的区域，时刻由插值得到）：

```json
{
  "safe": false,
  "revision": 1,
  "checked_at": "2026-09-30T03:40:00.000000Z",
  "results": [
    {
      "region_id": 1,
      "region_name": "A区-焊接作业面",
      "crossings": [
        {
          "entry_time": "2026-09-30T00:00:15Z",
          "exit_time": "2026-09-30T00:00:45Z",
          "entry_inclusive": true,
          "exit_inclusive": true,
          "point_contact": false
        }
      ]
    }
  ]
}
```

* `safe=true` 当且仅当所有区域的 `crossings` 均为空。
* `entry_inclusive/exit_inclusive` 标明端点开闭；穿越被生效窗口右端截断时
  `exit_inclusive=false`。
* 无穿越的区域也会出现在 `results` 中（`crossings: []`），保证逐区域可核对。

### 安全出发时刻 `POST /api/safe-departures`

用于场地调度：在候选出发时段内找出固定行程的**所有**安全出发时刻。

请求：

* `points`：固定行程，使用**相对秒数** `t`（区别于 `/api/verify` 的绝对时刻）。
  首点 `t` 必须为 `0`，其余 `t` 严格递增、为有限数；相邻点直线匀速。
  出发时刻记为 `d`，则相对时间 `t` 的轨迹点在 UTC 时刻 `d + t` 出现。
* `window_start` / `window_end`：带时区的候选出发窗口，**左闭右开**
  `[window_start, window_end)`；服务端统一换算为 UTC。

```json
{
  "points": [
    {"x": -50, "y": 50, "t": 0},
    {"x": 150, "y": 50, "t": 60}
  ],
  "window_start": "2026-08-31T23:58:00Z",
  "window_end":   "2026-08-31T23:59:30Z"
}
```

计算是**精确的解析区间运算，不做离散抽样**。设行程对某区域的一段几何接触为
相对时间闭区间 `[a, b]`（`a == b` 即单点接触），区域生效窗为 `[ws, we)`，
则平移后的接触与生效窗相交（含端点语义）当且仅当出发时刻
`d ∈ [ws - b, we - a)`。对所有区域、所有接触的"禁止出发区间"取并，再对候选
窗口取补集，即得全部安全区间。

响应（每个安全区间固定为左闭右开，端点均为 UTC；无可行时 `intervals` 为空数组）：

```json
{
  "revision": 1,
  "window_start": "2026-08-31T23:58:00Z",
  "window_end":   "2026-08-31T23:59:30Z",
  "intervals": [
    {
      "start": "2026-08-31T23:58:00Z",
      "end":   "2026-08-31T23:59:15Z",
      "start_inclusive": true,
      "end_inclusive": false
    }
  ]
}
```

边界与端点语义（与 `/api/verify` 完全一致，均精确处理而非近似）：

* **含边界**：区域多边形含边界；沿边界运动、与顶点的单点接触都计入禁止区间；
* **单点接触恰逢生效端点**：接触落在 `valid_from`（闭）的出发时刻被排除，
  落在 `valid_to`（开）的出发时刻允许；
* **候选窗口端点**：安全区间一律 `[start, end)`。窗口起点若等于某禁止区间的
  开端点则仍安全（含窗口起点），等于闭左端点则不安全；窗口终点始终不含；
* 多个区域的禁止区间重叠或半开相接时先合并，安全区间为补集后的各连通段。

该接口为只读：一次请求在单个 `REPEATABLE READ READ ONLY` 事务中读取同一已提交
快照，区域并发登记/修订/删除不影响本次结果，响应 `revision` 标明所用集合版本。
轨迹点少于 2 个、坐标或相对时刻非有限（NaN/Infinity）、首点 `t` 非 0、相对时刻
非严格递增、候选窗口不递增（`window_start >= window_end`）或窗口时刻无时区，
均以 `422` 拒绝整个请求。

### 错误响应

统一为 `422`（语义错误）或 `404`（区域不存在）：

```json
{
  "error": {
    "code": "validation_error",
    "message": "请求参数校验失败",
    "details": [{"loc": ["body", "polygon"], "message": "...", "type": "..."}]
  }
}
```

## 调用示例

```bash
# 端到端 curl 示例：登记两个区域、核验穿越/单点接触、修订与删除
bash examples/curl_examples.sh

# 仅用 Python 标准库的调用示例
python3 examples/python_client.py
```

## 测试

```bash
# 纯几何引擎（含安全出发区间解析用例，不需要数据库）
python3 tests/test_geometry.py

# HTTP + PostgreSQL 集成测试（需要可连接的数据库，会清空 regions 表）
DATABASE_URL="postgresql://restrict@localhost:5432/restrict" python3 tests/test_api.py
```

## 项目结构

```
app/
  main.py        # FastAPI 路由、事务边界、错误处理
  schemas.py     # Pydantic 请求/响应模型与输入校验
  geometry.py    # 轨迹-多边形穿越核验引擎（插值、裁剪、合并）
  repository.py  # 区域 CRUD 与一致性快照读取
  db.py          # psycopg3 连接池、建表初始化、启动重试
examples/        # curl 与 Python 调用示例
tests/           # 几何离线测试与 API 集成测试
Dockerfile, docker-compose.yml
```
