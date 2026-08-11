# 金融多 Agent 协作研报生成系统

> 6 个专职 AI Agent 通过 LangGraph 图编排协作：大纲设计 → 信息检索 → 财务分析 → 图表生成 → 报告撰写 → 质量评审，约 100 秒（API）或 8-10 分钟（本地模型）输出一份完整的深度研究报告。

---

## 多 Agent 协作架构

### 6 个专职 Agent

| Agent | 角色 | 职责 | 能力 |
|---|---|---|---|
| **chief_architect** | 架构师 | 解析用户需求，产出结构化研报大纲 | LLM 生成 6-8 章 Markdown 大纲 |
| **deep_scout** | 侦察兵 | 网络信息检索与综合 | function calling 自主决定搜索角度（当前复用 DuckDuckGo） |
| **chief_data_engineer** | 数据工程师 | 获取真实财务数据并解读 | AKShare 3 源竞速（东方财富/新浪/同花顺）+ yfinance |
| **data_analyst** | 数据分析师 | 数据可视化 | LLM 生成图表规格 → matplotlib 渲染 SVG |
| **chief_researcher** | 研究员 | 撰写完整研究报告 | 融合全部 Agent 产出成 3000-7000 字报告 |
| **critic_master** | 评审官 | 质量把关 | 5 维度打分，不合格触发研究员重写（最多 2 次） |

### 协作机制

```
                    ┌─ chief_architect ──────────┐
START → parse → Send│─ deep_scout ───────────────├→ data_analyst → chief_researcher → critic_master → END
                    └─ chief_data_engineer ──────┘                              ↑                      ↓ 不通过
                                                                         条件回滚 └──── retry (≤2) ──────┘
```

- **并行 fan-out**：3 个无依赖 Agent 通过 LangGraph `Send()` 并行执行，全部完成才汇合
- **共享数据总线**：Agent 间通过 `intermediate` 字典间接通信（写方 `save_intermediate`，读方 `get`），互不感知彼此存在
- **条件回滚**：评审不合格 → 状态字段驱动条件边跳回研究员重写，直至通过或达上限
- **Agent 工具权**：deep_scout 具备 function calling——LLM 自主决定调哪个工具、搜几个角度、传什么参数

## STAR 项目简历

### Situation

传统金融研究流程依赖分析师人工完成：阅读财报 → 搜索行业动态 → 整理数据 → 撰写报告 → 复核质量。一份完整研报需要 4-8 小时，且覆盖广度和数据时效性受限于个人能力。

需要构建一个**多 Agent 协作系统**，在分钟级内完成全流程，输出质量接近初级分析师水平。

### Task

| 挑战 | 具体问题 |
|---|---|
| **Agent 编排** | 6 个 Agent 存在数据依赖（搜索先于研究、财务先于图表），如何高效调度 |
| **并行加速** | 无依赖 Agent 如何并行，把耗时压到 100 秒级 |
| **质量可控** | LLM 可能输出幻觉或遗漏信息，如何自动检测并触发重写 |
| **数据工程** | 金融 API 有反爬 + 频率限制，MySQL JSON 列无法存 numpy 类型 |
| **平台稳定** | 8 次线上故障：LLM 乱码、Session 挂死、搜索崩溃、后台任务被 GC 回收 |

### Action

**1. 多 Agent 编排（核心）**

- Phase 1 通过 LangGraph `Send()` 并行无依赖 Agent，Phase 2 串行满足数据链依赖
- 条件边实现质量回滚：评审失败 → 自动重置研究员重写（最多 2 次）
- 自定义 `_merge_dict` reducer 自动合并并行分支状态更新
- 共享数据总线（intermediate dict）承载 Agent 间全部数据流

**2. Agent 工程能力**

| 能力 | 实现 |
|---|---|
| **function calling** | `_call_llm_with_tools()`：模型自主选择工具 + 参数，同轮工具并行执行，max_rounds 兜底；失败自动降级硬编码流程 |
| **多模型后端** | 一键切换 DeepSeek API / 本地 Qwen2.5-3B-Instruct（transformers 加载，bf16 GPU 推理，线程锁串行化） |
| **工程韧性** | 8 次故障修复（见 issues.md），成功率 100% |

**3. 性能优化**

- `asyncio.gather` 并行 Phase 1 3 Agent（加速比 2.6x）
- `_compact_financial()` 压缩财务 prompt 97K → 900 字符
- 内存缓存（TTL=300s）避免重复爬取
- 数据源竞速 + 线程池隔离同步库调用

**4. 安全修复（高危）**

- text2sql：f-string 拼接 → SQLAlchemy 参数化查询 + 只允许 SELECT
- `run_chart_code`：任意代码执行 → 预定义模板函数，彻底移除 subprocess

### Result

| 指标 | 数据 | 说明 |
|---|---|---|
| **API 模式总耗时** | **100.4s** | 3 轮 LangGraph 压测均值（P51-P53），成功率 100% |
| **本地模型总耗时** | **~430s** | Qwen2.5-3B-Instruct 压测 2 轮（P60-P61），全部 Agent success |
| **Agent 成功率** | **100%** | 全部 Agent success，无回滚超过上限 |
| **报告长度** | 3000-7000 字 | API 可达 7000+，本地模型受 max_tokens(4096) 限制 |
| **数据源** | 3 类 | 网络搜索(DuckDuckGo) + A 股行情(AKShare 3 源竞速) + 财报(yfinance) |
| **故障恢复** | 8/8 | 全部已修复并验证，无复发 |

## 技术栈

| 层 | 技术 |
|---|---|
| **AI 编排** | LangGraph (StateGraph + Send + conditional_edges) |
| **LLM** | 本地 Qwen2.5-3B-Instruct (transformers, bf16 GPU) / DeepSeek Chat API（可切换） |
| **后端** | Python 3.13 + FastAPI + SQLAlchemy 2.0 + PyMySQL |
| **前端** | React 18 + TypeScript + Ant Design 5 + Vite |
| **数据** | AKShare(东方财富/新浪/同花顺) + yfinance + DuckDuckGo |
| **可视化** | matplotlib 渲染 SVG 图表 |
| **数据库** | MySQL 8.0 (JSON 列, 时区自动转 Asia/Shanghai) |

## 架构

```
用户 ──► 前端 (React + Ant Design) ── POST /api/projects ──► FastAPI
                                                              │
                                                    ┌─────────┴──────────┐
                                                    │  工作流线程          │
                                                    │  LangGraph invoke  │
                                                    │    │               │
                                                    │ Parse ─► Send ×3  │
                                                    │    │  ┌──────┐    │
                                                    │    │  │Arch  │    │
                                                    │    ├──│Scout │    │
                                                    │    │  │Eng   │    │
                                                    │    │  └──────┘    │
                                                    │    ▼              │
                                                    │ Analyst →Writer   │
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
    agents/base/base_agent.py          — Agent 基类（_call_llm / function calling）
    llm/local_qwen.py                  — 本地 Qwen 推理（transformers GPU）
    core/state.py                      — 状态模型
    models/                            — ORM 模型
frontend/
  src/pages/TaskManage.tsx             — 项目管理页面
  src/components/MainLayout.tsx        — 布局组件
```

## 运行

```bash
# 后端（端口 8001）
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8001

# 前端
cd frontend
npm install
npm run dev      # 开发
npm run build    # 构建
```

### LLM 后端切换

`.env` 配置：

```
# 本地模型（默认）
LLM_PROVIDER=local_qwen
LOCAL_MODEL_PATH=C:/Users/xxx/.cache/modelscope/models/Qwen--Qwen2.5-3B-Instruct/snapshots/master

# 或 DeepSeek API
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
```