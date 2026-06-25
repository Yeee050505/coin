# 金融多 Agent 研报协同生成系统

> 基于 LangGraph 编排 6 个 AI Agent，自动完成"需求理解 → 信息检索 → 财务分析 → 报告撰写 → 质量评审"全流程，约 100 秒输出 7000+ 字深度研究报告。

---

## STAR 项目简历

### Situation

传统金融研究流程依赖分析师人工完成：阅读财报 → 搜索行业动态 → 整理数据 → 撰写报告 → 复核质量。一份完整研报需要 4-8 小时，且覆盖广度和数据时效性受限于个人能力。

需要构建一个**自动化多 Agent 系统**，能在分钟级内完成全流程，且输出质量接近初级分析师水平。

### Task

| 挑战 | 具体问题 |
|---|---|
| **Agent 编排** | 6 个 Agent 有数据依赖（搜索必须先于研究，财务数据必须先于图表），如何高效调度 |
| **并行加速** | 无依赖的 Agent 如何并行以压到 100 秒以内 |
| **质量可控** | LLM 可能输出幻觉或遗漏关键信息，如何自动检测并触发重写 |
| **数据工程** | 金融 API 有反爬 + 频率限制，MySQL JSON 列无法存 numpy 类型 |
| **平台稳定** | 8 次线上故障：LLM 乱码、Session 挂死、搜索崩溃、后台任务被 GC 回收 |

### Action

#### 1. 多 Agent 编排（核心创新）

```
START → [Send × 3 并行] → data_analyst → chief_researcher → critic_master → [END / retry]
         ├─ chief_architect      ← LLM 生成 8 章大纲
         ├─ deep_scout           ← Tavily + DuckDuckGo 并行检索
         └─ chief_data_engineer  ← AKShare 3 源竞速获取财务数据
```

- Phase 1 通过 LangGraph `Send()` 实现无依赖 Agent 并行，Phase 2 串行满足数据链依赖
- 条件边实现质量回滚：`critic_master` 评审失败 → 自动重置 `chief_researcher` 重写（最多 2 次）
- 自定义 `_merge_dict` reducer 自动合并并行分支的状态更新，无需手写 save 循环

#### 2. 工程韧性（8 次故障修复 → 100% 成功率）

| 故障 | 根因 | 修复 |
|---|---|---|
| DB 挂死 | `numpy.int64` / `NaN` 写入 MySQL JSON 列崩溃，session 污染导致所有后续操作卡死 | `_json_safe()` 递归清洗 numpy/pandas/NaN → `_save_task` 加 try/commit + rollback |
| LLM 乱码 | PowerShell 管道非 UTF-8 编码，中文被破坏 | `httpx` 直连 + `json.dumps(ensure_ascii=True)` 显式控制编码 |
| 任务不执行 | `asyncio.create_task` 无引用被 GC 回收 | 模块级 `_background_tasks` set 保持引用 |
| 搜索崩溃 | `web_search` 隐式返回 `None`，下游 `r.get("results")` 报错 | 兜底 return + 判空保护 |
| 无限轮询 | 异常路径使用污染 session，`p.status` 未持久化 | 所有异常路径使用独立 `get_session()` |

#### 3. 性能优化（120s → 100s - 仅 LLM 瓶颈）

- `asyncio.gather` 并行 Phase 1 3 Agent（加速比 2.6x）
- `_compact_financial()` 压缩财务 prompt 从 97K → 900 字符
- 3 路搜索查询并行 + 财务数据并行爬取
- 内存缓存（TTL=300s）避免重复爬取

#### 4. 安全修复（高危）

- text2sql：f-string 拼接 → SQLAlchemy 参数化查询 + 只允许 SELECT
- `run_chart_code`：任意代码执行 → 预定义模板函数，彻底移除 subprocess

### Result

| 指标 | 数据 | 说明 |
|---|---|---|
| **平均总耗时** | **100.4s** | 6 轮压测均值（3 轮手写 + 3 轮 LangGraph） |
| **成功率** | **100%** | 6/6 轮全部成功，6/6 Agent success |
| **报告质量** | **7000+ 字** | 包含基本面、行业、财务、估值、技术面、风险 6 章 |
| **数据源** | 4 类 | 网络搜索(Tavily/DDGS) + 股市API(AKShare 3源) + yfinance + RAG知识库 |
| **基础设施** | 3 个 | 业务监控轮询、Dashboard 聚合统计、批量管理 |
| **故障恢复** | 8/8 | 全部已修复并验证，无复发 |

---

## 技术栈

| 层 | 技术 |
|---|---|
| **AI 编排** | LangGraph 1.2.6 (StateGraph + Send + conditional_edges) |
| **LLM** | DeepSeek Chat API via httpx |
| **后端** | Python 3.13 + FastAPI + SQLAlchemy 2.0 + PyMySQL |
| **前端** | React 18 + TypeScript + Ant Design 5 + Vite |
| **数据** | AKShare(东方财富/新浪/同花顺) + yfinance + Tavily + DuckDuckGo |
| **可视化** | matplotlib 渲染 SVG 图表 |
| **数据库** | MySQL 8.0 (JSON 列) |

## 架构

```
用户 ──► 前端 (React + Ant Design) ── POST /api/projects ──► FastAPI
                                                               │
                                                     ┌─────────┴──────────┐
                                                     │  守护线程           │
                                                     │  asyncio loop      │
                                                     │  LangGraph invoke  │
                                                     │    │               │
                                                     │ Parse ─► Send ×3  │
                                                     │    │  ┌──────┐    │
                                                     │    │  │Arch  │    │
                                                     │    ├──│Scout │    │
                                                     │    │  │Eng   │    │
                                                     │    │  └──────┘    │
                                                     │    ▼              │
                                                     │ Analyst → Writer  │
                                                     │    → Critic → END │
                                                     │       ↻ (≤2)     │
                                                     └──────────────────┘
```

## 项目结构

```
backend/
  agent_core/
    scheduler_agent/graph_builder.py   — LangGraph 状态图（编排核心）
    sub_agents/                        — 6 个 Agent 业务逻辑
  app/
    api/                               — REST 接口
    tools/                             — AKShare 封装 + 工具注册
    agents/base/base_agent.py          — Agent 基类
    core/state.py                      — 状态模型
    models/                            — ORM 模型
frontend/
  src/pages/TaskManage.tsx             — 项目管理页面
  src/components/MainLayout.tsx        — 布局组件
```

## 运行

```bash
cd backend
# 安装依赖
pip install -r requirements.txt
# 启动（端口 8001）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

```bash
cd frontend
npm install
npm run dev      # 开发
npm run build    # 构建
```
