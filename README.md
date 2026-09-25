# 金融多 Agent 协作研报生成系统

> 一个由 **Supervisor 主控 + 7 个专职 worker Agent** 组成的智能金融研究系统。主控 LLM 在运行时自主编排：决定派遣哪个 Agent、什么顺序、迭代几轮；支持 critic 反馈驱动的**多轮追问改写**。用户提交研究主题，系统自动输出完整深度研究报告。

---

## 多 Agent 协作架构

### 主控 + 7 个专职 worker

| Agent | 角色 | 职责 | 核心能力 |
|---|---|---|---|
| **supervisor** | 主控/编排者 | 运行时动态决策：派遣哪个 worker、顺序、轮次 | 状态快照 + LLM 决策循环（≤8 轮）；本地 Qwen 走 ReAct JSON 协议，DeepSeek 走原生 function calling |
| **chief_analyst** | 首席分析师 | 解析需求，产出结构化研报大纲 | LLM 生成 6-8 章 Markdown 大纲 |
| **data_engineer** | 数据工程师 | 获取真实财务数据并解读 | AKShare 3 源竞速（东财/新浪/同花顺）+ yfinance，缓存 300s |
| **quant_analyst** | 量化分析师 | 数据可视化 | LLM 生成图表规格 → matplotlib 渲染 SVG |
| **fundamental_analyst** | 基本面分析师 | 公司基本面深度分析 | 财务指标、估值模型、护城河分析 |
| **news_analyst** | 新闻分析师 | 行业与公司新闻追踪 | 联网搜索（Bing CN）+ 新闻聚合、事件驱动分析、政策影响评估 |
| **technical_analyst** | 技术分析师 | 技术指标与K线分析 | K线形态、MACD/RSI/布林带等技术指标 |
| **senior_researcher** | 高级研究员 | 撰写完整研究报告 | 融合全部 Agent 产出；支持修改要求注入 |
| **compliance_officer** | 合规审核官 | 质量把关与追问驱动 | 5 维度打分 + 携带上次反馈复查；score≥55 视为通过 |

### 协作机制

```
                 ┌─── supervisor 主控循环（≤8 轮）────┐
                 │  每轮: 阶段状态快照 → LLM 决策     │
                 │  ├─ run_chief_analyst     ─────────┤
                 │  ├─ run_data_engineer     ─────────┤ ← 顺序由 LLM 动态决定
                 │  ├─ run_quant_analyst     ─────────┤   （实测 3B 自主规划
                 │  ├─ run_fundamental       ─────────┤    researcher→engineer→
                 │  ├─ run_news              ─────────┤    analyst→...）
                 │  ├─ run_technical         ─────────┤
                 │  ├─ run_researcher → 自动送审 ─────┤
                 │  └─ run_compliance ←────┘  │       │
                 │                         │ 未通过  │
                 │        ┌── 多轮追问 ────┘         │
                 │        ▼                          │
                 │  run_researcher(instruction=反馈) │
                 │      → run_compliance(复查上轮)    │
                 │  审查通过 → END                     │
                 └───────────────────────────────────┘
```

- **动态编排**：主控每轮读取状态快照（大纲/搜索/财务/图表/草稿/审查），自主选择下一个 worker，不依赖写死的边
- **多轮追问闭环**：草稿自动送审；未通过时主控带 compliance 反馈重派 researcher，compliance 携带上次反馈核对修复情况，迭代至通过
- **共享数据总线**：Agent 间通过 `intermediate` 字典间接通信（写方 `save_intermediate`，读方 `get`）
- **可靠性防线**：阶段门控（决策无效自动按序推进）+ 重复守卫 + 固定顺序兜底流水线，小模型决策失效也能 100% 产出报告
- **DeepSeek 双重兜底**：本地推理失败/空输出 → 自动重试 DS API；本地评审连续 2 次未通过 → 研究员+评审强制切 DS 重写再审
- **用户侧多轮追问**：`POST /api/projects/{id}/followup` 逐轮对已有报告提问 —— 采用轻量 Q&A 模式直接作答（不重生成整篇报告）；会话隔离 + 最近 4 轮滑动窗口上下文；每轮独立落库 `followup_N`，前端详情弹窗逐轮展示
- **per-agent 延迟追踪**：每个 Agent 执行自动记录 `started_at`/`completed_at`，API 返回 `duration_s`，后端日志汇总打印耗时

---

## STAR 项目简历

### Situation

传统金融研究依赖分析师人工完成：读财报 → 搜行业动态 → 整理数据 → 撰写报告 → 复核质量，一份完整研报需 4-8 小时，覆盖广度与时效性受限于个人能力。需要构建一个多 Agent 协作系统，分钟级完成全流程，输出接近初级分析师水平的报告。

### Task

| 挑战 | 具体问题 |
|---|---|
| **Agent 编排** | 7 个 worker Agent 存在数据依赖（财务先于图表），且流程在运行时才确定 |
| **动态调度** | 固定 DAG 无法满足"运行时决定谁先谁后、要不要再搜一轮"的自主协作诉求 |
| **质量可控** | LLM 可能输出幻觉或遗漏，如何自动检测并驱动重写收敛 |
| **数据工程** | 金融 API 反爬 + 频率限制；MySQL JSON 列无法存 numpy/pandas 类型 |
| **本地化运行** | 无 API 依赖也能跑：8GB 显存 + 16GB 内存环境下的本地小模型全流程 |
| **平台稳定** | 历史 28 次线上故障：LLM 乱码、Session 挂死、搜索崩溃、后台任务被 GC 回收等 |

### Action

**1. Supervisor 多 Agent 编排（核心）**

- 主控循环：状态快照 → LLM 决策 → 派遣 worker → 回填状态，直到审查通过
- worker 工具化：7 个专职 Agent 封装为可调用工具，主控自主组合
- 多轮追问：researcher 自动送审 + compliance 反馈记忆，未通过带要求重写，直至收敛
- 三道防线保障可靠性：阶段门控 / 重复守卫 / 兜底流水线

**2. Agent 工程能力**

| 能力 | 实现 |
|---|---|
| **function calling** | 双协议：DeepSeek 原生 tools / 本地 Qwen ReAct JSON；模型自主选工具 + 参数，max_rounds 兜底 |
| **多模型后端** | `LLM_PROVIDER` 一键切换 DeepSeek API / 本地 Qwen2.5-3B-Instruct / `auto` 智能路由（transformers, bf16 GPU, 线程锁串行化） |
| **任务路由器** | `auto` 模式三层分类：①Agent白名单（大纲/摘要/图表JSON/技术解读/主控决策→3B）②启发式（>3000字或因果/交叉比对关键词→API）③3B分类器兜底；复杂推理（估值/新闻/写作/审校）转发 DeepSeek |
| **多轮追问** | compliance 携上次反馈复查，supervisor 带 instruction 重派研究员 |
| **工程韧性** | 28 次故障修复（issues.md），压测成功率 100% |

**3. 性能优化**

- `asyncio.gather` 并行无依赖调用、`_compact_financial()` 压缩财务 prompt 97K→900 字符
- 数据源竞速（东财/新浪/同花顺）+ 内存缓存 TTL=300s + 线程池隔离同步库
- 多源降级链应对国内源不稳定：行情 新浪→东财、资金流 东财→同花顺全市场、板块行情 东财push2→同花顺指数、港股 东财→新浪；失败重试 + 兜底缓存
- 每个工作流独占 daemon 线程 + 独立 event loop，崩溃互不影响

**4. 安全修复（高危）**

- text2sql：f-string 拼接 → SQLAlchemy 参数化 + 只允许 SELECT
- `run_chart_code`：任意代码执行 → 预定义图表模板，彻底移除 subprocess

### Result

| 指标 | 数据 | 说明 |
|---|---|---|
| **API 模式总耗时** | **~100s** | LangGraph 压测（P51-P53），成功率 100% |
| **本地模型总耗时** | **~430s** | Qwen2.5-3B-Instruct（P60-P61），全部 Agent success |
| **Supervisor 压测** | **P62-P67 全绿** | 本地 3B 6 轮：主控自主编排（P64-P67 无兜底）、审查收敛（P66 首轮通过 75 分 / P67 DS 升级后通过 72 分）、报告最长 10912 字 |
| **DeepSeek 兜底** | P67 实测 | 本地评审连挂 2 次自动切 DS 重写再审，一次通过；报告 10912 字 |
| **Agent 成功率** | **100%** | 全部 Agent success |
| **报告长度** | 3000-10912 字 | 本地受 max_tokens(4096) 限制；DS 兜底重写可达万字级 |
| **数据源** | 6 类 | 网络搜索(Bing CN) + A 股行情/资金流/估值分位 + 财报与财务比率 + 券商研报评级 + 财经快讯/行业板块（AKShare 多源降级） |
| **故障恢复** | 28/28 | 全部修复并验证无复发 |
| **per-agent 延迟** | 3-142s | quant_analyst 最快(3s)，senior_researcher 最慢(142s) |

---

## 技术栈

| 层 | 技术 |
|---|---|
| **AI 编排** | LangGraph (StateGraph + Supervisor 主控循环) |
| **LLM** | 本地 Qwen2.5-3B-Instruct (transformers, bf16 GPU) / DeepSeek Chat API（`LLM_PROVIDER`: deepseek / local_qwen / auto 智能路由） |
| **后端** | Python 3.13 + FastAPI + SQLAlchemy 2.0 + PyMySQL |
| **前端** | React 18 + TypeScript + Ant Design 5 + Vite |
| **数据** | AKShare（东财/新浪/同花顺，行情/资金流/估值/研报/快讯/港股/财务比率，多源降级）+ yfinance + Bing CN 搜索 |
| **可视化** | matplotlib 渲染 SVG 图表 |
| **数据库** | MySQL 8.0（JSON 列，时区自动转 Asia/Shanghai） |

## 架构

```
用户 ──► 前端 (React + Ant Design) ── POST /api/projects ──► FastAPI
                                                              │
                                                    ┌─────────┴──────────┐
                                                    │  工作流线程          │
                                                    │  LangGraph invoke  │
                                                    │    │               │
                                                    │ Parse ─► Supervisor│
                                                    │   ┌── 主控循环 ────┐│
                                                    │   │ 快照→决策→派遣  ││
                                                    │   │ Arch/Scout/Eng ││
                                                    │   │ Analyst/Writer ││
                                                    │   │ →Critic(追问↻) ││
                                                    │   └───────┬────────┘│
                                                    │        END          │
                                                    └────────────────────┘
  DB: research_projects / research_tasks（JSON 列）
  报告: backend/app/reports/report_{id}.md
```

## 项目结构

```
backend/
  agent_core/
    scheduler_agent/graph_builder.py   — LangGraph 状态图（Supervisor 编排核心）
    sub_agents/                        — supervisor 主控 + 7 个 worker Agent
      supervisor_agent.py              — 主控：decide() 决策 + worker 工具定义
      chief_architect.py               — 首席分析师（大纲生成）
      data_engineer.py                 — 数据工程师（财务数据）
      data_analyst.py                  — 量化分析师（图表生成）
      fundamental_analyst.py           — 基本面分析师
      news_analyst.py                  — 新闻分析师
      technical_analyst.py             — 技术分析师
      chief_researcher.py              — 高级研究员（报告撰写）
      critic_master.py                 — 合规审核官（质量把关）
  app/
    api/                               — REST 接口（projects / reports / dashboard）
    tools/                             — AKShare 封装 + 工具注册（7 个工具）
    agents/base/base_agent.py          — Agent 基类（_call_llm / function calling 双协议）
    llm/local_qwen.py                  — 本地 Qwen 推理（transformers GPU 单例）
    llm/router.py                      — LLM 任务路由（简单→3B / 复杂→API, 三层分类）
    core/state.py                      — ResearchState 状态模型
    models/                            — ORM 模型
frontend/
  src/pages/TaskManage.tsx             — 项目管理页面（CRUD + 轮询 + 下载）
  src/components/MainLayout.tsx        — 布局组件
```

## 运行

```bash
# 后端（端口 8001，跑本地模型需 GPU 环境）
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8001

# 前端
cd frontend
npm install
npm run dev      # 开发
npm run build    # 构建
```

### LLM 后端切换（backend/.env）

```
# 智能路由（推荐）: 简单任务→本地3B, 复杂推理→DeepSeek
LLM_PROVIDER=auto
LOCAL_MODEL_PATH=C:/Users/xxx/.cache/modelscope/models/Qwen--Qwen2.5-3B-Instruct/snapshots/master
DEEPSEEK_API_KEY=sk-xxx

# 或强制单一后端
LLM_PROVIDER=local_qwen   # 全部本地
LLM_PROVIDER=deepseek     # 全部 API
```

### 创建研究任务

```bash
curl -X POST http://127.0.0.1:8001/api/projects \
  -H "Content-Type: application/json" \
  -d '{"title":"宁德时代(300750)投资价值分析","description":"分析宁德时代的财务表现、行业地位与投资建议","scenario":"financial_research"}'
```

### 项目列表（分页）

```bash
# 默认每页10条，按创建时间倒序
curl "http://127.0.0.1:8001/api/projects?page=1&size=10"

# 返回格式
{
  "items": [...],
  "total": 21,
  "page": 1,
  "size": 10,
  "pages": 3
}
```

### Agent 延迟追踪

```bash
# 项目详情中每个 Agent 包含 duration_s 字段
curl "http://127.0.0.1:8001/api/projects/71" | jq '.agent_statuses[] | {agent_name, duration_s}'

# 输出示例
{"agent_name": "quant_analyst", "duration_s": 3.0}
{"agent_name": "senior_researcher", "duration_s": 142.0}
```

后端日志汇总：
```
[latency] agent durations (total 326s):
  senior_researcher:      142s
  data_engineer:          37s
  quant_analyst:          38s
  fundamental_analyst:    23s
  news_analyst:           17s
  technical_analyst:      11s
  chief_analyst:          14s
```

### 对已有报告追问（Q&A 多轮）

```bash
curl -X POST http://127.0.0.1:8001/api/projects/67/followup \
  -H "Content-Type: application/json" \
  -d '{"question":"爱尔眼科未来的分红能力和派息潜力如何？"}'
# 回答落库为 followup_N 任务行，GET /api/projects/67 的 tasks 中可见
```

---

## LoRA 微调训练

react_protocol LoRA 微调已完成，显著提升本地 Qwen2.5-3B 的工具调用可靠性：

| 指标 | 基座模型 | + LoRA adapter |
|------|---------|---------------|
| 总通过率 | 29/42 (69.0%) | **39/42 (92.9%)** |
| JSON 格式合法率 | 38/42 (90.5%) | **42/42 (100%)** |
| 工具选择正确率 | 33/42 (78.6%) | **39/42 (92.9%)** |
| 参数正确率 | 29/42 (69.0%) | **39/42 (92.9%)** |
| eval_loss | — | 0.0130 |
| adapter 大小 | — | 14 MB |

```bash
# 测试（42 条域外用例）
cd backend && python -m training.test_adapter_42
```

详见 `backend/training/TEST_REPORT.md`

---

## 文档

- `ARCHITECTURE.md` — 架构细节：调度机制、状态设计、压测量化（API/本地/Supervisor 三套数据）
- `issues.md` — 28 个问题记录与修复（含 Supervisor 改造全过程 P62-P66、DeepSeek 双重兜底 P67、用户侧 Q&A 追问 P67-followup、per-agent 延迟追踪、Bing 搜索切换）
- `backend/training/TEST_REPORT.md` — react_protocol LoRA 微调测试报告
