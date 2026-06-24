# 架构 — 多 Agent 智能金融研究系统

## 系统目的

基于多 Agent 协作的智能金融研究系统，自动完成「需求理解 → 信息检索 → 数据分析 → 报告撰写 → 质量评审」全流程。用户只需提交研究主题，系统在约 **100 秒** 内输出深度研究报告（约 7000 字），覆盖公司基本面、行业分析、财务解读、业绩归因和投资建议。

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| **前端** | React 18, TypeScript, Ant Design 5, Vite, Axios | SPA，轮询更新状态 |
| **后端** | Python 3.13, FastAPI, SQLAlchemy 2.0, PyMySQL | REST API，端口 8001 |
| **Agent 框架** | 自定义异步多 Agent（非 LangGraph） | `asyncio.gather` + 串行循环 |
| **LLM** | DeepSeek Chat API（`deepseek-chat`） via `httpx` | 直连 HTTP，规避 OpenAI SDK 编码问题 |
| **数据源** | AKShare（东方财富/新浪/同花顺）, yfinance, Tavily 搜索 API | 多源竞争/故障转移模式 |
| **数据库** | MySQL 8.0，JSON 列 | `research_projects`、`research_tasks` 表 |
| **任务编排** | `asyncio` 事件循环 + `ThreadPoolExecutor` | 每个工作流独占一个守护线程 |

## 整体架构

```
用户 ──► 前端 (React + Ant Design)
            │ POST /api/projects {title, description}
            ▼
         FastAPI 后端 (main.py:8001)
            │
            │ 1. 创建 ResearchProject 记录 (status=pending)
            │ 2. 启动 daemon threading.Thread
            ▼
         ┌─ 守护线程 ──────────────────────────────┐
         │  asyncio.new_event_loop()               │
         │    └─ WorkflowGraph.run(project_id, req)│
         │         │                               │
         │    ┌────┴──────────────┐                │
         │    │ Phase 1 — 并行    │ asyncio.gather │
         │    │ chief_architect   │                │
         │    │ deep_scout        │                │
         │    │ chief_data_engineer│               │
         │    └────┬──────────────┘                │
         │         ▼                               │
         │    ┌────┴──────────────┐                │
         │    │ Phase 2 — 串行                     │
         │    │ data_analyst      │                │
         │    │ chief_researcher  │                │
         │    │ critic_master     │◄── 回滚 ──────│
         │    └────┬──────────────┘ (最多 2 次)    │
         │         ▼                               │
         │    保存最终报告到 DB + .md 文件           │
         └─────────────────────────────────────────┘
            │
            ▼
         前端每 3 秒轮询 GET /api/projects/:id
         直到 status ≠ (pending|running)
```

## Agent 工作流

六个 Agent 分为两个阶段执行：

### 阶段 1 — 并行（`asyncio.gather`）

| Agent | 职责 | 输出 |
|---|---|---|
| **chief_architect** | LLM 生成研究大纲 | Markdown 章节结构 |
| **deep_scout** | 多源网络搜索（Tavily + DuckDuckGo）+ RAG 检索 | 搜索综合文本 |
| **chief_data_engineer** | 通过 AKShare/yfinance 获取财务数据 + LLM 解读 | 精简财务分析 |

三者并发执行，全部完成后将输出保存到 `research_tasks` 表。

### 阶段 2 — 串行（带重试/回滚）

| 步骤 | Agent | 输入依赖 |
|---|---|---|
| 1 | **data_analyst** | 财务数据 → LLM 生成图表规格 → `run_chart_code` 工具渲染 SVG |
| 2 | **chief_researcher** | 大纲 + 搜索综合 + 财务解读 → LLM 撰写完整报告 |
| 3 | **critic_master** | 草稿报告 → LLM 评审 → `passed`/`failed` + 评分 |

### 回滚流程

```
critic_master.review_passed == false 且 retry_count < MAX_RETRIES (2)
  → 重置 chief_researcher 任务状态
  → 重新执行 chief_researcher（复用相同的大纲/搜索/财务数据）
  → 重新执行 critic_master
  → 最多重复 2 次
```

### Agent 执行循环（`BaseAgent.run`）

```
execute()       → Agent 特有逻辑（LLM 调用 + 工具调用）
  ↓
reflect()       → 验证输出（基础实现：检查非空）
  ↓
保存到状态      → state.agent_tasks[name].output_data
```

## Agent 调度机制

### 调度器核心（`WorkflowGraph.run`）

```
┌─ IntentParser.parse(request) ──► stock_codes + dimensions
│
├─ 初始化 ResearchState (project_id, title, request, stock_codes)
│
├─ Phase 1 — 并行调度 ── asyncio.gather() ───────────────────┐
│   ├─ chief_architect.run(state)                             │  同时执行
│   ├─ deep_scout.run(state)                                  │  互不依赖
│   └─ chief_data_engineer.run(state)                         │  仅读 request
│                                                             │
│   ▼ 全部完成后                                              │
│   └─ 串行 _save_task() × 3  ← 同一 db_session               │
│                                                             │
├─ Phase 2 — 串行调度（依赖链）                                │
│   ├─ data_analyst.run(state)        需要 data_engineer 输出 │
│   ├─ chief_researcher.run(state)    需要所有 phase1 输出    │
│   └─ critic_master.run(state)       需要 researcher 输出    │
│       │                                                     │
│       └─ review_passed == false ?                           │
│           └─ retry < 2 → 重置 chief_researcher → 重试       │
│                                                             │
└─ 更新 project.status = success/failed                       │
```

### 生命周期与错误隔离

```
POST /api/projects
  │
  ├─ 主线程: 创建 DB 记录, 返回 project_id
  │
  └─ 子线程 (threading.Thread, daemon=True)
       │
       ├─ local_db = get_session()        ← 新建专用 session
       ├─ loop = asyncio.new_event_loop() ← 新建事件循环
       │
       ├─ graph_builder.run()
       │   ├─ _save_task(local_db, ...)   ← 每个 agent 完成后保存
       │   │   ├─ try: db_session.commit()
       │   │   └─ except: rollback + raise
       │   ├─ asyncio.gather()            ← phase1 并发
       │   ├─ sequential loop             ← phase2 带重试
       │   └─ return result
       │
       ├─ status_db = get_session()       ← 新 session 写最终状态
       ├─ p.status = "success"
       ├─ write report_$id.md 到磁盘
       └─ status_db.commit() + close()
```

### 调度三原则

1. **线程隔离** — 每个项目独占一个 daemon 线程 + 独立 event loop，一个项目崩溃不影响其他项目
2. **阶段边界保存** — 每个 agent 完成后立即 `_save_task` 写入 MySQL，即使后续 agent 失败也不会丢失已完成的结果
3. **故障恢复** — `_save_task` 内部 try/commit + rollback 保护 session；projects.py 的异常路径使用独立 `get_session()` 防止 session 污染

## 各 Agent 目的与职责

| Agent | 目的 | 核心能力 |
|-------|------|----------|
| **chief_architect** | 理解用户需求，制定研究框架和报告大纲 | LLM 生成 8 章结构，覆盖产品概况、业绩、持仓、策略、风险、持有人、对比、结论 |
| **deep_scout** | 多源网络检索，收集行业动态和最新信息 | Tavily API + DuckDuckGo 并行搜索，RAG 知识库检索 |
| **chief_data_engineer** | 获取真实财务数据并做初步解读 | AKShare（3 源 failover）获取行情/财报/yfinance 国际数据，LLM 分析趋势 |
| **data_analyst** | 数据可视化，生成图表 | LLM 生成 python 代码 → `run_chart_code` 工具 → matplotlib 渲染 SVG |
| **chief_researcher** | 综合所有信息撰写完整深度研究报告 | LLM 融合 3 个 phase1 输出，生成 7000+ 字结构化报告 |
| **critic_master** | 质量评审，控制是否回退重写 | LLM 评分 + review_passed 开关，触发 critic→researcher 回滚（最多 2 次） |

## 量化成果（基准测试）

3 次连续压测（项目 P46-P48），全部成功，数据如下：

| 指标 | 均值 | 最小值 | 最大值 | 说明 |
|------|------|--------|--------|------|
| **阶段 1 耗时** | 23.4s | 20.1s | 25.1s | 3 Agent 并行：架构师 + 侦察兵 + 数据工程师 |
| **阶段 2 耗时** | 30.1s | 30.1s | 30.2s | 串行：数据分析师 + 研究员 + 评论家（研究员 LLM 占 ~25s） |
| **总耗时** | 103.8s | 100.4s | 110.5s | API 创建 → status=success |
| **任务成功率** | 100% | — | — | 6/6 Agent 全部 success |
| **创建响应** | 0.03s | — | — | API 立即返回 project_id，异步执行 |

**故障修复前后对比（P41 vs P32）：**
- 修复前：P32 卡在第 3 个 Agent（DB JSON 序列化失败 → session 挂死），前端无限轮询
- 修复后：P41-P48 全部 103s 内完成，报告写入磁盘，前端正常显示完成

## 文件映射

```
backend/
  main.py                          — FastAPI 入口，生命周期，静态资源挂载
  app/
    api/
      projects.py                  — POST/GET/DELETE 项目接口 + 工作流线程启动
      reports.py                   — GET /api/reports/:id 报告查询与下载
      dashboard.py                 — GET /api/dashboard/summary 仪表盘
    models/
      __init__.py                  — SQLAlchemy 引擎、session 工厂、init_db()
      database.py                  — ResearchProject、ResearchTask ORM 模型（JSON 列）
    schemas/
      common.py                    — Pydantic 请求/响应模型
    agents/
      base/
        base_agent.py              — BaseAgent: _call_llm(), run(), execute() 抽象, reflect()
    tools/
      financial_api.py             — AKShare（东方财富/新浪/同花顺）+ yfinance 封装，_race()，缓存
      registry.py                  — 工具装饰器 + 调度（web_search, fetch_financial_data, run_chart_code）
    core/
      config.py                    — .env 配置（API 密钥、数据库 URL）
      state.py                     — ResearchState、AgentTaskState（Pydantic 模型）
    reports/                       — 生成的 .md 报告文件
  agent_core/
    scheduler_agent/
      graph_builder.py             — WorkflowGraph：解析 → 阶段 1 并行 → 阶段 2 串行 → 保存
      intent_parser.py             — 从用户请求中提取股票代码/分析维度
    sub_agents/
      chief_architect.py           — 阶段 1：研究大纲（LLM）
      deep_scout.py                — 阶段 1：网络搜索 + RAG（Tavily/DDGS）
      data_engineer.py             — 阶段 1：AKShare/yfinance 数据 + LLM 分析
      data_analyst.py              — 阶段 2：图表生成（LLM + matplotlib）
      chief_researcher.py          — 阶段 2：完整报告撰写（LLM）
      critic_master.py             — 阶段 2：质量评审 + 回滚决策（LLM）
frontend/
  src/
    App.tsx                        — 路由（单路由：/）
    components/MainLayout.tsx      — 侧边栏 + 顶栏 + 内容区，仪表盘轮询
    pages/TaskManage.tsx           — CRUD 表格、创建弹窗、详情弹窗、3 秒轮询
    services/api.ts                — Axios 客户端（baseURL /api）
```

## 关键设计决策

### 为什么 AKShare 使用 `asyncio` + `ThreadPoolExecutor`

AKShare 是同步库（封装 `requests`）。直接在 asyncio 事件循环中运行会阻塞循环。采用 `loop.run_in_executor(_single_executor, sync_fn)` 模式将 AKShare 调用卸载到专用线程池，同时保持 Agent 框架的异步性。

### 为什么阶段 1 并行、阶段 2 串行

阶段 1 的 Agent（架构师、侦察兵、数据工程师）彼此没有数据依赖——它们都只读取原始用户请求——因此可以通过 `asyncio.gather` 并行运行。阶段 2 的 Agent 有严格的数据依赖：`data_analyst` 需要 `financial_data`，`chief_researcher` 需要所有阶段 1 的输出，`critic_master` 需要草稿报告。串行执行还能支持回滚循环（重新执行 `chief_researcher` → `critic_master`）。

### 为什么需要 `_json_safe`

MySQL `JSON` 列和 SQLAlchemy 的 JSON 类型无法存储 `numpy.int64`、`numpy.float64`、`pandas.Timestamp` 或 `NaN/Inf` 浮点值。`_json_safe()` 函数递归地将这些值转换为原生 Python 类型（`int`、`float`、`str`、`None`），防止 JSON 编码错误。

### 为什么每个数据库路径都使用新会话

工作流在守护线程中运行，自带 asyncio 循环。SQLAlchemy 会话不是线程安全的，并且携带身份映射状态。每个操作（任务保存、状态更新、错误处理）都通过 `get_session()` 打开新会话，可以避免跨异步边界的事务状态污染和过期对象问题。

### 为什么 AKShare 调用使用 `_single_executor`

Python 3.13 在 Windows 上存在线程池与 asyncio 交互的已知问题，当多个线程共享同一个执行器时尤为突出。使用 `ThreadPoolExecutor(max_workers=1)`（`_single_executor`）序列化 AKShare 调用，这是可以接受的，因为 AKShare 内部会限制 API 请求频率。这样可以避免 Windows 上 concurrent.futures + asyncio 桥接中的死锁和竞态条件。
