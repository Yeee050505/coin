# 架构 — 多 Agent 智能金融研究系统

## 系统目的

基于多 Agent 协作的智能金融研究系统，自动完成「需求理解 → 信息检索 → 数据分析 → 报告撰写 → 质量评审」全流程。用户只需提交研究主题，系统在约 **100 秒** 内输出深度研究报告（约 7000 字），覆盖公司基本面、行业分析、财务解读、业绩归因和投资建议。

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| **前端** | React 18, TypeScript, Ant Design 5, Vite, Axios | SPA，轮询更新状态 |
| **后端** | Python 3.13, FastAPI, SQLAlchemy 2.0, PyMySQL | REST API，端口 8001 |
| **Agent 框架** | LangGraph 1.2.6 (`StateGraph`) | Supervisor 主控循环：状态快照 + LLM 决策派遣 worker |
| **LLM** | DeepSeek Chat API（`deepseek-chat`） via `httpx` | 直连 HTTP，规避 OpenAI SDK 编码问题 |
| **数据源** | AKShare（东方财富/新浪/同花顺）, yfinance, Tavily 搜索 API | 多源竞争/故障转移模式 |
| **数据库** | MySQL 8.0，JSON 列 | `research_projects`、`research_tasks` 表 |
| **任务编排** | `threading.Thread` + `asyncio.new_event_loop()` 调用 LangGraph | 每个工作流独占一个守护线程 |

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
         ┌─ 守护线程 ─────────────────────────────────┐
         │  asyncio.new_event_loop()                  │
         │    └─ WorkflowGraph.run(project_id, req)   │
         │         │                                  │
         │    ┌────┴──────────────┐                   │
         │    │ LangGraph START   │                   │
         │    │    └─ parse       │                   │
         │    │         │         │                   │
         │    │    supervisor ◄───┤                   │
         │    │   主控循环(≤8轮)  │ LLM 每轮决策       │
         │    │   ├─ run_architect / run_scout        │
         │    │   ├─ run_data_engineer / run_analyst  │
         │    │   ├─ run_researcher ─┐                │
         │    │   └─ run_critic ◄────┘ auto review    │
         │    │         │ 追问: 反馈→重写→复查       │
         │    │         ▼                             │
         │    │    END                                │
         │    └───────────────────────────────────────┤
         │    保存最终报告到 DB + .md 文件            │
         └────────────────────────────────────────────┘
            │
            ▼
         前端每 3 秒轮询 GET /api/projects/:id
         直到 status ≠ (pending|running)
```

## Agent 工作流

### Supervisor 主控模式（v2，替代固定 DAG）

图结构简化为 `START → parse → supervisor → END`。`supervisor` 节点内部跑受控编排循环（≤8 轮）：

```
每轮：
  1. 组装阶段状态快照（大纲/搜索/财务/图表/草稿/审查分数+反馈）
  2. Supervisor LLM 决策：派遣哪个 worker（工具形式）或完成
  3. 执行对应 worker（WorkerAgent 通过 _to/_from_research_state 桥接复用）
  4. 快照反映新状态，LMM 基于此做下一轮决策
  5. run_researcher 后自动触发 critic（保证审查闭环）
```

| worker 工具 | 对应 Agent | 说明 |
|---|---|---|
| run_architect | chief_architect | 生成大纲 |
| run_scout | deep_scout | 多角度搜索（内部 FC 自主定角度） |
| run_data_engineer | chief_data_engineer | 拉财务数据 + 解读 |
| run_analyst | data_analyst | 图表 |
| run_researcher | chief_researcher | 写报告；args.instruction 携带修改要求 |
| run_critic | critic_master | 审查；携带上次反馈复查 |

### 多轮追问闭环

```
run_researcher ──► auto critic_master
                        │ passed=False（带反馈）
                        ▼
supervisor 决策：run_researcher(instruction=critic反馈) ──► auto critic_master（复查上轮问题）
                        │
                        ▼
                迭代直至 passed 或轮次耗尽（≤8 轮）
```

`critic_previous_feedback` 存入 intermediate，critic 复查时核对"上次问题是否修复"，使追问收敛（本地 3B 压测：加入阈值 score≥55 视为通过后，P66 首轮审查即通过）。

### 可靠性防线（针对本地 3B 小模型）

| 防线 | 行为 |
|---|---|
| **阶段门控** | 模型决策无效 / 乱 finish 时，自动按规范顺序（architect→scout→engineer→analyst→researcher）推进下一缺省阶段 |
| **重复守卫** | 同一 worker 决策连续 ≥2 次 → 强制兜底流水线（auto critic 后重置计数，避免误伤合法追问） |
| **兜底流水线** | 主控完全失效时顺序执行完整流程 + 一轮 feedback 修订 |
| **critic 阈值** | score ≥ 55 视为通过（3B 评审过严会烧光轮次） |

### Agent 执行循环（`BaseAgent.run`）

```
execute()       → Agent 特有逻辑（LLM 调用 + 工具调用）
  ↓
reflect()       → 验证输出（基础实现：检查非空）
  ↓
保存到状态      → state.agent_tasks[name].output_data
```

## LangGraph 状态设计

```
GraphState (TypedDict):
  project_id, title, original_request, stock_codes — 输入字段
  status            — 最终状态 (由 run() 在 invoke 后设置)
  intermediate      — Annotated[dict, _merge_dict]   ← 各 agent 共享的数据平面
  agent_outputs     — Annotated[dict, _merge_dict]   ← 各 agent 的输出收集
  final_report      — 最终报告文本 (chief_researcher 设置)
  review_passed / review_feedback / review_score / review_attempts
                    — 评审回滚控制 (critic_master 设置)
```

核心设计要点：
- `intermediate` 和 `agent_outputs` 使用 `_merge_dict` reducer，使得 Send 并行分支的状态更新自动合并，不会冲突
- 非 reducer 字段（`final_report`, `review_passed` 等）仅在串行阶段由特定 agent 写入，不会出现多分支并发写
- `db_session` 不进入 GraphState（JSON 不安全），通过 WorkflowGraph 实例变量闭包传递

## 调度机制

### 调度器核心（`WorkflowGraph.run` → LangGraph `StateGraph`）

```
START
  │
  ▼
parse — IntentParser.parse(request) → stock_codes
  │
  ▼
supervisor（主控循环，≤8 轮）
  │  每轮: _supervisor_snapshot(gs) → SupervisorAgent.decide(LLM)
  │        → 派遣 worker（_node_agent 执行）→ 回填状态
  │        run_researcher 后自动 run critic（携上次反馈复查）
  │
  ▼
END → run() 设置 status = success/completed_with_issues
```

### 状态转换适配层

每个 LangGraph 节点内部：

```
LangGraph GraphState → _to_research_state() → ResearchState (Pydantic)
     ↓
agent.run(ResearchState)  ← 复用现有 BaseAgent 逻辑，零改动
     ↓
_from_research_state() → dict update → 合并回 LangGraph GraphState
```

`_to_research_state`：从 TypedDict 重建 Pydantic ResearchState，包括 agent_tasks
`_from_research_state`：按 agent 名选择性提取变更（避免并行分支写非 reducer 字段冲突）

### 生命周期与错误隔离

```
POST /api/projects
  │
  ├─ 主线程: 创建 DB 记录, 返回 project_id
  │
  └─ 子线程 (threading.Thread, daemon=True)
       │
       ├─ local_db = get_session()         ← 新建专用 session
       ├─ loop = asyncio.new_event_loop()  ← 新建事件循环
       │
       ├─ graph_builder.run()
       │   ├─ parse node (IntentParser)    ← 提取 stock_codes
       │   ├─ supervisor 主控循环          ← LLM 动态编排 ≤8 轮
       │   │    └─ 派遣 worker：architect / scout / engineer / analyst / researcher / critic
       │   │    └─ researcher 后自动 critic，未通过带反馈重派（追问）
       │   └─ return result
       │
       ├─ status_db = get_session()        ← 新 session 写最终状态
       ├─ p.status = "success"
       ├─ write report_$id.md 到磁盘
       └─ status_db.commit() + close()
```

### 调度三原则

1. **线程隔离** — 每个项目独占一个 daemon 线程 + 独立 event loop，一个项目崩溃不影响其他项目
2. **阶段边界保存** — 每个 agent 节点完成后立即 `_save_task` 写入 MySQL，即使后续 agent 失败也不会丢失已完成的结果
3. **故障恢复** — `_save_task` 内部 try/commit + rollback 保护 session；projects.py 的异常路径使用独立 `get_session()` 防止 session 污染

## 各 Agent 目的与职责

| Agent | 目的 | 核心能力 |
|-------|------|----------|
| **supervisor** | 主控编排，运行时决定派遣哪个 worker、顺序与轮次 | 状态快照 + LLM 决策循环（本地 ReAct JSON / DeepSeek 原生 tools），多轮追问驱动 |
| **chief_architect** | 理解用户需求，制定研究框架和报告大纲 | LLM 生成 8 章结构，覆盖产品概况、业绩、持仓、策略、风险、持有人、对比、结论 |
| **deep_scout** | 多源网络检索，收集行业动态和最新信息 | function calling 自主调用 web_search（DuckDuckGo） |
| **chief_data_engineer** | 获取真实财务数据并做初步解读 | AKShare（3 源 failover）获取行情/财报/yfinance 国际数据，LLM 分析趋势 |
| **data_analyst** | 数据可视化，生成图表 | LLM 生成图表规格 → `run_chart_code` 工具 → matplotlib 渲染 SVG |
| **chief_researcher** | 综合所有信息撰写完整深度研究报告 | LLM 融合各 Agent 产出，支持 research_instruction 修改要求注入 |
| **critic_master** | 质量评审，控制是否回退重写 | LLM 评分 + review_passed 开关 + 上次反馈复查（score≥55 视为通过） |

## 量化成果

### LangGraph 版压测（P51-P53）

| 指标 | 均值 | 最小值 | 最大值 | 说明 |
|------|------|--------|--------|------|
| **阶段 1 耗时** | 28.4s | 25.1s | 35.1s | Send 并行：架构师 + 侦察兵 + 数据工程师 |
| **阶段 2 耗时** | 35.1s | 30.1s | 45.1s | 串行：数据分析师 + 研究员 + 评论家（研究员 LLM 占 ~25s） |
| **总耗时** | 100.4s | 95.4s | 110.4s | API 创建 → status=success |
| **任务成功率** | 100% | — | — | 6/6 Agent 全部 success |
| **创建响应** | <0.05s | — | — | API 立即返回 project_id，异步执行 |

### 对比原始 asyncio 版（P46-P48）

| 指标 | asyncio 版 | LangGraph 版 | 变化 |
|------|-----------|-------------|------|
| **平均总耗时** | 103.8s | 100.4s | -3.3% |
| **Phase 1 均值** | 23.4s | 28.4s | +21%（LLM 响应波动） |
| **Phase 2 均值** | 30.1s | 35.1s | +17%（LLM 响应波动） |
| **成功率** | 100% (3/3) | 100% (3/3) | = |
| **Agent success** | 6/6 | 6/6 | = |

总耗时差异在 LLM 响应时间正常波动范围内（±15%），两个版本实际性能持平。

### 故障修复前后对比（P41 vs P32）

- 修复前：P32 卡在第 3 个 Agent（DB JSON 序列化失败 → session 挂死），前端无限轮询
- 修复后：P41-P48 + P51-P53 全部 110s 内完成，报告写入磁盘，前端正常显示完成

### 本地模型压测（P60-P61：Qwen2.5-3B-Instruct，bf16 GPU）

| 指标 | P60 | P61 | 均值 | 说明 |
|------|-----|-----|------|------|
| **模型加载** | ~17s | —（热启动复用） | ~17s | 首次调用 lazily 加载，~10GB 显存占用 |
| **阶段 1 耗时** | ~72s | ~95s | ~84s | 并行 3 Agent + 图表；DDGS 搜索抖动 |
| **研究员生成** | ~236s | ~200s | ~218s | 单次 4096 token 上限生成，本地推理速度瓶颈 |
| **总耗时** | ~458s | ~400s | ~430s | API 创建 → 全部 Agent success |
| **任务成功率** | 100% | 100% | 100% | 6/6 Agent 全部 success |
| **报告长度** | 3140 字 | 2970 字 | ~3050 字 | 受 max_new_tokens=4096 上限约束 |

对比说明：本地 3B 模型推理速度约 25-35 token/s（RTX 4060 Laptop），Phase 2 研究员单次长文本生成为主要瓶颈（占总量 ~50%）。P61 在 6 Agent 全 success 后因机器定时关机被强杀，最终状态由 DB 数据恢复补写。

### Supervisor 主控压测（P62-P66：本地 Qwen2.5-3B，依次修复过程）

| 项目 | rounds | fallback | 审查 | 报告字数 | 说明 |
|---|---|---|---|---|---|
| P62 比亚迪 | — | ✅ | 2 次修订 | 5310 | 映射 bug 前旧代码，全兜底路径 |
| P63 茅台 | — | ✅ | 2 次修订 | — | 同上 |
| P64 腾讯 | 8 | ✅(守卫) | 3 次未过 | 3813 | 模型全自主决策（scout→engineer→architect→analyst→researcher），映射 bug 修复 |
| P65 招行 | 8 | ❌ | 4 次未过 | 3823 | 守卫修复，追问循环跑满无兜底 |
| P66 隆基绿能 | 6 | ❌ | ✅ 通过(75) | 7752 | critic 阈值(≥55)生效，首轮审查收敛 |

结论：
- 本地 3B 可完成**完整自主编排**（自主规划顺序、带反馈重派研究员）+ **多轮追问闭环**（critic 携上次反馈复查，P66 一次收敛）
- 阶段门控 + 兜底流水线保证 100% 产出报告（5/5 success）
- critic 严格度需阈值收敛，否则追问循环烧满 8 轮（P65 验证守卫不误伤但轮次仍耗尽）

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
      graph_builder.py             — WorkflowGraph：LangGraph StateGraph 定义
      intent_parser.py             — 从用户请求中提取股票代码/分析维度
    sub_agents/
      supervisor_agent.py           — Supervisor 主控（decide() + worker 工具定义）
      chief_architect.py            — worker：研究大纲（LLM）
      deep_scout.py                 — worker：网络搜索（function calling / DDGS）
      data_engineer.py              — worker：AKShare/yfinance 数据 + LLM 分析
      data_analyst.py               — worker：图表生成（LLM + matplotlib）
      chief_researcher.py           — worker：完整报告撰写（LLM）
      critic_master.py              — worker：质量评审 + 追问反馈（LLM）
frontend/
  src/
    App.tsx                        — 路由（单路由：/）
    components/MainLayout.tsx      — 侧边栏 + 顶栏 + 内容区，仪表盘轮询
    pages/TaskManage.tsx           — CRUD 表格、创建弹窗、详情弹窗、3 秒轮询
    services/api.ts                — Axios 客户端（baseURL /api）
```

## 关键设计决策

### 为什么使用 LangGraph 替代手写 asyncio

原始方案使用 `asyncio.gather` + `while` 循环手写编排。LangGraph 带来以下优势：

- **显式有向图** — 节点和边一目了然，无需阅读循环逻辑理解调度顺序
- **内置 fan-out / fan-in** — `Send()` 自动处理并行分支的数据合并（reducer 模式），无需手写 gather + save 循环
- **声明式回滚** — `conditional_edges` 替代 `while retry_count` 循环，图结构天然避免缩进过深
- **状态一致性** — `Annotated[dict, reducer]` 保证并行分支对同一 state key 的更新自动合并

### 为什么保留 BaseAgent.run 不变

LangGraph 节点内部通过 `_to_research_state()` / `_from_research_state()` 适配层桥接，不对 Agent 代码做任何修改。这样：
- 每个 Agent 的 `execute()`、`reflect()`、`_call_llm()` 逻辑完全保留
- 回滚方案只需切回同文件旧版本，无需修改 6 个 Agent
- 后续增加新 Agent 只需加一行 `builder.add_node`

### 为什么 Send 分支只返回 reducer 字段

LangGraph StateGraph 中，非 `Annotated` 字段在同一 step 中只能接收一次写入。`Send` 并行分支如果同时写 `status`/`final_report` 等无 reducer 字段会抛出 `InvalidUpdateError`。`_from_research_state()` 按 agent 名选择性返回字段：
- Phase 1 节点只返回 `intermediate` + `agent_outputs`（都有 reducer）
- `chief_researcher` 额外返回 `final_report`
- `critic_master` 额外返回 `review_passed`/`review_feedback`/`review_score`/`review_attempts`

### 为什么 AKShare 使用 `asyncio` + `ThreadPoolExecutor`

AKShare 是同步库（封装 `requests`）。直接在 asyncio 事件循环中运行会阻塞循环。采用 `loop.run_in_executor(_single_executor, sync_fn)` 模式将 AKShare 调用卸载到专用线程池，同时保持 Agent 框架的异步性。

### 为什么阶段 1 并行、阶段 2 串行

（v1 设计）阶段 1 的 Agent（架构师、侦察兵、数据工程师）彼此没有数据依赖——它们都只读取原始用户请求——因此可以通过 **Send** 并行运行。阶段 2 的 Agent 有严格的数据依赖：`data_analyst` 需要 `financial_data`，`chief_researcher` 需要所有阶段 1 的输出，`critic_master` 需要草稿报告。串行执行还能支持回滚循环（重新执行 `chief_researcher` → `critic_master`）。

### 为什么升级为 Supervisor 主控（v2）

固定 DAG 无法满足"运行时才知道流程"的多 Agent 诉求（issues #22/#24）。改为 Supervisor 模式后：
- **动态编排** — 主控 LLM 每轮读取状态快照自主选择 worker，实测本地 3B 会自行规划完整顺序，无需写死边
- **多轮追问** — researcher 自动送审，critic 携上次反馈复查，未通过由主控带 instruction 重派研究员，直至收敛
- **保留可靠性** — 阶段门控 + 重复守卫 + 兜底流水线三道防线，小模型决策失效时自动降级，压测 5/5 成功
- **代价** — 牺牲并行 fan-out（各阶段串行派遣），本地模式每轮决策 ~10s；API 模式可用并行 worker 回归优化

### 为什么需要 `_json_safe`

MySQL `JSON` 列和 SQLAlchemy 的 JSON 类型无法存储 `numpy.int64`、`numpy.float64`、`pandas.Timestamp` 或 `NaN/Inf` 浮点值。`_json_safe()` 函数递归地将这些值转换为原生 Python 类型（`int`、`float`、`str`、`None`），防止 JSON 编码错误。

### 为什么每个数据库路径都使用新会话

工作流在守护线程中运行，自带 asyncio 循环。SQLAlchemy 会话不是线程安全的，并且携带身份映射状态。每个操作（任务保存、状态更新、错误处理）都通过 `get_session()` 打开新会话，可以避免跨异步边界的事务状态污染和过期对象问题。

### 为什么 AKShare 调用使用 `_single_executor`

Python 3.13 在 Windows 上存在线程池与 asyncio 交互的已知问题，当多个线程共享同一个执行器时尤为突出。使用 `ThreadPoolExecutor(max_workers=1)`（`_single_executor`）序列化 AKShare 调用，这是可以接受的，因为 AKShare 内部会限制 API 请求频率。这样可以避免 Windows 上 concurrent.futures + asyncio 桥接中的死锁和竞态条件。

### 为什么不用 LangGraph Checkpointer

LangGraph 内置的 Checkpointer 是为持久化历史状态（用于状态回放/人机交互）设计的，需要引入存储后端（SQLite/PostgreSQL）。本系统对历史状态无需求——`research_tasks` 表已承担业务层面的持久化。引入 Checkpointer 会增加 JSON 序列化适配工作且无收益。
