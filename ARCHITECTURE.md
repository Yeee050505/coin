# Architecture — Multi-Agent Financial Research System

## 系统目的

基于多 Agent 协作的智能金融研究系统，自动完成「需求理解 → 信息检索 → 数据分析 → 报告撰写 → 质量评审」全流程。用户只需提交研究主题，系统在约 **100 秒** 内输出深度研究报告（约 7000 字），覆盖公司基本面、行业分析、财务解读、业绩归因和投资建议。

## Tech Stack

| Layer | Technology | Details |
|---|---|---|
| **Frontend** | React 18, TypeScript, Ant Design 5, Vite, Axios | SPA with polling-based status updates |
| **Backend** | Python 3.13, FastAPI, SQLAlchemy 2.0, PyMySQL | REST API on port 8001 |
| **Agent Framework** | Custom async multi-agent (not LangGraph despite directory name) | `asyncio.gather` + sequential loop |
| **LLM** | DeepSeek Chat API (`deepseek-chat`) via `httpx` | Direct HTTP calls (avoids OpenAI SDK encoding issues) |
| **Data Sources** | AKShare (东方财富/新浪/同花顺), yfinance, Tavily Search API | Multi-source with race/failover pattern |
| **Database** | MySQL 8.0 with JSON columns | `research_projects`, `research_tasks` tables |
| **Task Orchestration** | `asyncio` event loop + `ThreadPoolExecutor` | Daemon thread per workflow |

## Overall Architecture

```
User ──► Frontend (React + Ant Design)
            │ POST /api/projects {title, description}
            ▼
         FastAPI Backend (main.py:8001)
            │
            │ 1. Create ResearchProject row (status=pending)
            │ 2. Spawn daemon threading.Thread
            ▼
         ┌─ Daemon Thread ──────────────────────────────┐
         │  asyncio.new_event_loop()                    │
         │    └─ WorkflowGraph.run(project_id, request) │
         │         │                                    │
         │    ┌────┴────────────────┐                   │
         │    │  Phase 1 — Parallel │ asyncio.gather()  │
         │    │  chief_architect    │                   │
         │    │  deep_scout         │                   │
         │    │  chief_data_engineer│                   │
         │    └────┬────────────────┘                   │
         │         ▼                                    │
         │    ┌────┴────────────────┐                   │
         │    │  Phase 2 — Sequential                   │
         │    │  data_analyst       │                   │
         │    │  chief_researcher   │                   │
         │    │  critic_master      │◄── rollback ──────│
         │    └────┬────────────────┘  (max 2 retries)  │
         │         ▼                                    │
         │    Save final_report to DB + .md file        │
         └──────────────────────────────────────────────┘
            │
            ▼
         Frontend polls GET /api/projects/:id every 3s
         until status != (pending|running)
```

## Agent Workflow

Six agents execute across two phases:

### Phase 1 — Parallel (`asyncio.gather`)

| Agent | Role | Output |
|---|---|---|
| **chief_architect** | Generate research outline via LLM | Markdown chapter structure |
| **deep_scout** | Multi-source web search (Tavily + DuckDuckGo) + RAG retrieval | Search synthesis text |
| **chief_data_engineer** | Fetch financial data via AKShare/yfinance + LLM interpretation | Compact financial analysis |

All three run concurrently. After all complete, their outputs are saved to `research_tasks` table.

### Phase 2 — Sequential (with retry/rollback)

| Step | Agent | Inputs |
|---|---|---|
| 1 | **data_analyst** | Financial data → LLM generates chart specs → `run_chart_code` tool renders SVG |
| 2 | **chief_researcher** | Outline + search synthesis + financial interpretation → LLM writes full report |
| 3 | **critic_master** | Draft report → LLM review → `passed`/`failed` with score |

### Rollback Flow

```
critic_master.review_passed == false AND retry_count < MAX_RETRIES (2)
  → Reset chief_researcher task state
  → Re-run chief_researcher (reuses same outline/search/financial data)
  → Re-run critic_master
  → Repeat up to 2 times
```

### Agent Execution Loop (`BaseAgent.run`)

```
execute()       → agent-specific logic (LLM calls + tool calls)
  ↓
reflect()       → validate output (base: checks non-empty)
  ↓
Save to state   → state.agent_tasks[name].output_data
```

## Agent 调度机制

### 调度器核心（`WorkflowGraph.run`）

```
┌─ IntentParser.parse(request) ──► stock_codes + dimensions
│
├─ Init ResearchState (project_id, title, request, stock_codes)
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
│           └─ retry < 2 → reset chief_researcher → 重试       │
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
| **critic_master** | 质量评审，控制是否回退重写 | LLM 评分 + review_passed 开关，触发 crit→researcher 回滚（最多 2 次） |

## 量化成果（基准测试）

3 次连续压测（项目 P46-P48），全部成功，数据如下：

| 指标 | 均值 | 最小值 | 最大值 | 说明 |
|------|------|--------|--------|------|
| **Phase1 耗时** | 23.4s | 20.1s | 25.1s | 3 agent 并行：架构师+侦察+数据工程 |
| **Phase2 耗时** | 30.1s | 30.1s | 30.2s | 串行：分析师+研究员+评论家（研究员 LLM 占 ~25s） |
| **总耗时** | 103.8s | 100.4s | 110.5s | API create → status=success |
| **任务成功率** | 100% | — | — | 6/6 agent 全部 success |
| **创建响应** | 0.03s | — | — | API 立即返回 project_id，异步执行 |

**故障修复后对比（P41 与 P32 对比）：**
- 修复前：P32 卡在第 3 agent（DB JSON 序列化失败 → session 挂死），前台无限轮询
- 修复后：P41-P48 全部 103s 内完成，report 写入磁盘，前端正常显示完成

## File Map

```
backend/
  main.py                          — FastAPI entry, lifespan, static mount
  app/
    api/
      projects.py                  — POST /api/projects (creates + spawns thread)
                                     GET /api/projects, GET /api/projects/:id
                                     DELETE /api/projects/:id
      reports.py                   — GET /api/reports/:id, GET /api/reports/:id/download
      dashboard.py                 — GET /api/dashboard/summary
    models/
      __init__.py                  — SQLAlchemy engine, session factory, init_db()
      database.py                  — ResearchProject, ResearchTask ORM models (JSON columns)
    schemas/
      common.py                    — Pydantic request/response models
    agents/
      base/
        base_agent.py              — BaseAgent: _call_llm(), run(), execute() abstract, reflect()
    tools/
      financial_api.py             — AKShare (EM/Sina/THS) + yfinance wrappers, _race(), caching
      registry.py                  — Tool decorator + dispatch (web_search, fetch_financial_data, run_chart_code, etc.)
    core/
      config.py                    — Settings from .env (API keys, DB URL)
      state.py                     — ResearchState, AgentTaskState (Pydantic models)
    reports/                       — Generated .md report files
  agent_core/
    scheduler_agent/
      graph_builder.py             — WorkflowGraph: parse → phase1 parallel → phase2 sequential → save
      intent_parser.py             — Extract stock codes / dimensions from user request
    sub_agents/
      chief_architect.py           — Phase 1: research outline (LLM)
      deep_scout.py                — Phase 1: web search + RAG (Tavily/DDGS)
      data_engineer.py             — Phase 1: AKShare/yfinance data + LLM analysis
      data_analyst.py              — Phase 2: chart generation (LLM + matplotlib)
      chief_researcher.py          — Phase 2: full report composition (LLM)
      critic_master.py             — Phase 2: quality review + rollback decision (LLM)
frontend/
  src/
    App.tsx                        — Router (single route: /)
    components/MainLayout.tsx      — Sider + Header + Content, dashboard polling
    pages/TaskManage.tsx           — CRUD table, create modal, detail modal, 3s polling
    services/api.ts                — Axios client (baseURL /api)
```

## Key Design Decisions

### Why `asyncio` + `ThreadPoolExecutor` for AKShare

AKShare is a synchronous library (wraps `requests`). Running it directly in the asyncio event loop would block the loop. The pattern uses `loop.run_in_executor(_single_executor, sync_fn)` to offload AKShare calls to a dedicated thread pool while keeping the rest of the agent framework async.

### Why sequential vs parallel phases

Phase 1 agents (architect, scout, engineer) have no data dependencies on each other — they all read only the raw user request — so they can run in parallel via `asyncio.gather`. Phase 2 agents have strict data dependencies: `data_analyst` needs `financial_data`, `chief_researcher` needs all Phase 1 outputs, and `critic_master` needs the draft report. Sequential execution also enables the rollback loop (re-run `chief_researcher` → `critic_master`).

### Why `_json_safe`

MySQL `JSON` columns and SQLAlchemy's JSON type cannot store `numpy.int64`, `numpy.float64`, `pandas.Timestamp`, or `NaN/Inf` float values. The `_json_safe()` function recursively converts these to native Python types (`int`, `float`, `str`, `None`) before persisting, preventing `JSON_encode` errors.

### Why each DB path uses a fresh session

The workflow runs in a daemon thread with its own asyncio loop. SQLAlchemy sessions are not thread-safe and carry identity map state. Opening a new session via `get_session()` for each operation (task save, status update, error handling) avoids transaction state pollution and stale object issues across async boundaries.

### Why `_single_executor` for AKShare calls

Python 3.13 on Windows has known issues with thread pool + asyncio interactions when multiple threads share the same executor. Using `ThreadPoolExecutor(max_workers=1)` (`_single_executor`) serializes AKShare calls, which is acceptable because AKShare internally throttles API requests. This avoids deadlocks and race conditions in the concurrent.futures + asyncio bridge on Windows.
