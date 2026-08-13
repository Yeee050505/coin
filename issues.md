# 金融深度研究平台 — 问题记录

## 1. 创建时间对不上

**现象：** 前端显示的项目创建时间与用户本地时间（东八区）不一致。

**根因：** `database.py` 使用 `datetime.datetime.utcnow()` 返回无时区信息的 naive UTC 时间。Pydantic 序列化为 JSON 时不带时区标记（如 `"2026-06-23T14:00:00"`），前端 JS `new Date()` 解析行为在不同浏览器下不一致，导致时间偏移 8 小时。

**修复：**
- 新增 `TZDateTime` 自定义 SQLAlchemy TypeDecorator
- 存储时统一转为 UTC（timezone-aware）
- 查询时自动转换为 Asia/Shanghai（UTC+8）
- 使用 `datetime.now(timezone.utc)` 替代 `datetime.datetime.utcnow`

**涉及文件：** `app/models/database.py`

---

## 2. 下载报告入口缺失

**现象：** 详情弹窗中有报告内容但无下载按钮。

**修复：** 
- 在 `TaskManage.tsx` 详情弹窗中增加「下载报告」按钮
- 导入 `downloadReport` API 函数
- 显示报告内容区域（含 sections 渲染和纯文本回退）

**涉及文件：** `frontend/src/pages/TaskManage.tsx`

---

## 3. 数据是假的——Agent 全用硬编码模板

**现象：** 所有 6 个 Agent 输出的都是重复的固定文字（"基于多源数据综合分析和行业调研数据，该领域呈现稳定增长态势…"），没有调用任何 LLM。

**根因：** 项目骨架搭建完成后，Agent 的 `execute()` 方法直接返回硬编码字符串，从未连接过 DeepSeek API。

**修复：**
- `base_agent.py` 新增 `_call_llm()` 方法，通过 `httpx` 直接调用 DeepSeek API
- 所有 6 个 Agent 重写为调用 `_call_llm()` 生成动态内容
- 系统指令改为英文 + "Output in Chinese"，避免中文被 PowerShell 破坏

**涉及文件：**
- `app/agents/base/base_agent.py`
- `agent_core/sub_agents/*.py` (全部 6 个 Agent)

---

## 4. 财经 API 反爬——数据源不可用

**现象：** 东方财富接口被反爬拒绝，请求直接断连。

**根因：** AKShare 调用的东方财富 API 有反爬机制，太频繁会被封。

**修复：**
- 增加请求间隔限速（1.2s 防抖）
- 指数退避重试（最多 3 次，等待 2s→4s）
- 并行竞速三个数据源：东方财富 / 新浪 / 同花顺，谁先返回有效数据用谁
- 财务数据优先走新浪（支持季度粒度），同花顺做备选

**涉及文件：** `app/tools/financial_api.py`

---

## 5. 股票代码不识别

**现象：** 输入「完美世界 一季报」，系统无法识别这是股票代码 `002624`。

**修复：**
- `intent_parser.py` 新增公司名称 → 股票代码映射表
- `ResearchState` 增加 `stock_codes` 字段
- `graph_builder.py` 解析用户请求后传递股票代码
- `data_engineer.py` 使用提取到的股票代码拉取数据

**涉及文件：**
- `agent_core/scheduler_agent/intent_parser.py`
- `app/core/state.py`

---

## 6. 后台任务不执行——项目卡在 pending

**现象：** 创建项目后状态始终为 `pending`，后台工作流从未启动。

**根因：** `asyncio.create_task(run_workflow())` 创建的 Task 没有保持引用，被 Python 垃圾回收器回收了。这是 FastAPI + asyncio 的已知问题。

**修复：**
- 模块级 set `_background_tasks` 保存 Task 引用
- `add_done_callback(_background_tasks.discard)` 自动清理已完成的任务

**涉及文件：** `app/api/projects.py`

---

## 7. LLM 返回乱码（"您的消息是乱码"）

**现象：** Agent 调用 DeepSeek API 后，LLM 回复「您发送的内容是乱码」，内容中全是 `???`。  

**根因（关键）：** 
1. PowerShell 的 `@''...''@ | python -` here-string 在 Windows 上使用系统 locale 编码（如 cp1252），**不是 UTF-8**。当中文通过这种管道传给 Python 时会被破坏。
2. 所有 Agent 的 system prompt 中的中文字符都被写入了乱码字节，导致 LLM 收到的 prompts 全是不可识别的字符。

**修复：**
- `_call_llm` 改为直接使用 `httpx` 发起 HTTP 调用（绕过 `openai` 包的编码问题）
- 使用 `json.dumps(payload, ensure_ascii=True).encode("utf-8")` 显式控制 JSON 编码
- 所有 Agent 的 system prompt 改用英文 + "Output in Chinese" 指令
- `config.py` 中 `.env` 路径改为绝对路径，避免 CWD 依赖

**涉及文件：**
- `app/agents/base/base_agent.py`
- `app/core/config.py`
- `agent_core/sub_agents/*.py` (全部 6 个 Agent)

---

## 8. Git Revert 导致修改丢失

**现象：** 某次用户操作（或系统行为）执行了 `git checkout .` 或等价操作，导致所有中间修改被还原到原始版本。

**影响：** 第 3/4/5/6 项的修复全部丢失，base_agent.py 回到无 `_call_llm` 的版本，Agent 回到硬编码假数据。

**处理：** 重新应用所有修复。

---

## 9. main.py 端口被 git revert 改回 8000

**现象：** 服务器启动后报端口绑定错误，或在 8001 上无法连接。

**根因：** git revert 把 `main.py` 的 `port=8001` 还原成了 `port=8000`。

**修复：** 改回 `port=8001`。

## 11. 前端构建可能带过期缓存

**现象：** 后端返回正确的中文报告（6876 字符已验证），但前端仍显示 `？？？？？？`。

**可能原因：** `npm run build` 的 dist 产物没有被服务器更新加载，或浏览器缓存了旧的 JS chunk。

**处理：** 重新 `npm run build` 并重启服务器。

---

## 12. web_search 返回 None 导致 deep_scout 崩溃

**现象：** 日志报 `'NoneType' object has no attribute 'get'`，deep_scout Agent 执行失败。

**根因：**
1. `web_search()` 函数在 Tavily 和 DuckDuckGo 都失败时，没有显式 return，Python 隐式返回 `None`。
2. 原本的兜底 `return` 语句在 line 74，但位于 stock_ths 函数**之后**，属于不可达代码。
3. `deep_scout.py` 中 `r.get("results")` 未对 `None` 做防护。

**修复：**
- 将兜底 `return {"status": "unavailable", ...}` 移入 `web_search` 函数体内
- 删除 stock_ths 后的孤立 return
- `deep_scout.py` 中加 `r and` 判空

**涉及文件：**
- `app/tools/registry.py`
- `agent_core/sub_agents/deep_scout.py`

---

## 13. 前端源码中硬编码中文被破坏为 `????`

**现象：** 前端界面多处显示 `????` 而非中文（下载按钮、研究报告标题、暂无报告提示等）。

**根因：** 文件编码问题（可能是 Git revert 或非 UTF-8 保存）导致源码中的中文字符被破坏。

**修复：**
- `TaskManage.tsx` 修复 5 处 `????` → 正确中文
- `backend/app/api/projects.py` 注释 `"""?? API ???"""` → `"""项目 API 路由"""`
- `backend/app/tools/registry.py` 工具描述、知识库内容全部重写
- `backend/app/tools/financial_api.py` 列名、注释恢复中文
- `backend/app/api/dashboard.py` / `backend/app/rag/embedding.py` 注释修复
- `backend/main.py` port 8000 → 8001

**涉及文件：**
- `frontend/src/pages/TaskManage.tsx`
- `backend/app/api/projects.py`
- `backend/app/tools/registry.py`
- `backend/app/tools/financial_api.py`
- `backend/app/api/dashboard.py`
- `backend/app/rag/embedding.py`
- `backend/main.py`

---

## 14. 缺失依赖包 duckduckgo_search / yfinance

**现象：** 日志报 `No module named 'duckduckgo_search'` / `No module named 'yfinance'`，搜索和财务数据功能不可用。

**根因：** 项目使用 `.venv` 虚拟环境，但 `pip install` 安装到了 conda 全局环境，`.venv` 中没有这些包。

**修复：** 在 `.venv` 中执行 `pip install duckduckgo_search yfinance`。

**涉及文件：** 无（Python 包依赖）

---

## 15. SQLite 迁移 MySQL

**背景：** 原项目使用 SQLite（`research.db`），多进程并发时存在写锁问题，且本地数据不易管理。

**变更：**
- `.env` 中 `DATABASE_URL` 改为 `mysql+pymysql://root:123456@127.0.0.1:3306/coin?charset=utf8mb4`
- `app/models/__init__.py` 中 MySQL 使用 `pool_size=5, max_overflow=10, pool_pre_ping=True`，SQLite 专有的 `check_same_thread=False` 已隔离
- 安装 `pymysql` 依赖
- `main.py` 弃用 `@app.on_event("startup")`，改用 `lifespan` 上下文管理器
- `uvicorn.run(app, ...)` 改为 `uvicorn.run("main:app", ...)` 以支持 `reload=True`
- `.gitignore` 新增 `*.db` / `*.db-shm` / `*.db-wal`
- SQLite 文件已移至 `backup/sqlite_old/`

**涉及文件：**
- `backend/.env`
- `backend/app/models/__init__.py`
- `backend/main.py`
- `backend/requirements.txt`
- `.gitignore`

**注意：** 需要先在 MySQL 中创建 `coin` 库：
```sql
CREATE DATABASE coin CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

---

## 16. 安全漏洞：text2sql SQL 注入 + run_chart_code 任意代码执行

**严重程度：** 高危

**text2sql 漏洞：**
- 使用 f-string 拼接用户输入到 SQL 查询（`LIKE '%{query}%'`），虽有关键词黑名单但可被绕过
- `sqlite_master` 查询同样未做参数化

**修复：** 改用 SQLAlchemy `text()` + 绑定参数 `:kw`，消除注入风险；仅允许 SELECT 查询

**run_chart_code 漏洞：**
- 接收任意 Python 代码，在黑名单关键词检查后通过 `subprocess.run` 执行
- 黑名单（`os.`, `subprocess`, `exec`, `eval` 等）可通过 `getattr(__builtins__, ...)` 等方法轻松绕过
- 攻击者可执行任意系统命令

**修复：** 改为预定义图表模板函数，只接受 `chart_type`/`title`/`labels`/`values` 参数，彻底移除代码执行能力

**连带修复：**
- `base_agent.py` 删除未导入模块的 `_client` 类型标注（引用未 import 的 `openai`）
- `data_analyst.py` 改为引导 LLM 输出结构化 JSON 图表规格，不再生成 Python 代码

**涉及文件：**
- `backend/app/tools/registry.py`
- `backend/agent_core/sub_agents/data_analyst.py`
- `backend/app/agents/base/base_agent.py`

---

## 17. 财经数据爬取速度慢

**现象：** 一次研报任务中数据采集阶段耗时 10~20 秒。

**根因：**
1. `data_engineer.py` 中 `for code in ... for indicator in ...` 9 次 API 调用是**串行**的，每次等待 1~3 秒
2. 无缓存机制，每次请求都重新爬取
3. akshare 同步库通过线程池运行，线程不可取消，`_race` 即使先返回也无法中断其他源

**优化：**
- 改用 `asyncio.gather` 并行发起所有股票代码 × 指标的请求，总耗时从 O(n×m) 降为 O(max(n,m))
- 新增内存缓存 `_CACHE`（TTL=300s），同一数据 5 分钟内不再重复爬取
- 默认股票代码从 `["600519", "000858"]` 改为 `["002624"]`

**涉及文件：**
- `backend/agent_core/sub_agents/data_engineer.py`
- `backend/app/tools/financial_api.py`

---

## 18. 搜索服务不可用 + HuggingFace 被墙卡死

**现象：** DuckDuckGo 搜索失败（库改名 `ddgs` + 查询参数乱码）；HuggingFace embedding 模型下载连接超时，导致工作流卡死。

**修复：**
- 安装新的 `ddgs` 包，替换旧的 `duckduckgo_search`
- `retrieve_knowledge` 去掉 `sentence-transformers` 依赖，改用关键词匹配，彻底规避 HuggingFace 墙
- matplotlib 图表设置中文字体 `Microsoft YaHei`，避免图表标题变方块

**涉及文件：**
- `backend/app/tools/registry.py`

---

## 19. MySQL JSON 序列化异常导致工作流挂死

**现象：** 工作流执行到第 2 个 Agent（`deep_scout`）后整体挂死，后续 Agent 无法继续，前端无限轮询。

**根因：**
MySQL JSON 列（`ResearchTask.output_data`）无法序列化 numpy 类型（`numpy.int64`、`numpy.float64`、`numpy.datetime64`）、`float nan` / `float inf` 以及 pandas Timestamp。Agent 输出的中间结果中包含这些类型时，`json.dumps` → ORM commit 抛出 `TypeError` / `ValueError`。
连锁效应：`_save_task()` 没有 try/except commit，异常向上传播导致整个 `run()` 协程崩溃；异常路径使用了已污染的 `local_db` session，最终状态停留在 `"running"`。

**修复：**
1. `_json_safe()` 递归清洗：numpy 标量 → `.item()`，`NaN/Inf` → `None`，pandas Timestamp → `.to_pydatetime().isoformat()`
2. `_save_task()` 加 `try: commit() / except: rollback() + raise`
3. `projects.py` 成功/超时/异常三路径统一使用独立 `get_session()`，解决 session 污染
4. 移除冗余的最终 re-save 循环

**同步优化：**
- Phase 1 3 Agent `asyncio.gather` 并行（加速比 2.6x）
- `_compact_financial()` 压缩财务 prompt 97K → 900 字符
- Web 搜索 3 路并行

**涉及文件：**
- `backend/agent_core/scheduler_agent/graph_builder.py`
- `backend/app/api/projects.py`
- `backend/agent_core/sub_agents/deep_scout.py`
- `backend/agent_core/sub_agents/data_engineer.py`

---

## 20. chief_researcher LLM 幻觉股价数据

**现象：** 生成的研报中写当前股价 **6.00 元**、目标价 **7.50 元**，但实际永鼎股份(600105)实时股价为 **72.96 元**（同花顺数据源），偏差 10 倍。

**根因：**
数据链完整且正确：`AKShare(72.96) → _compact_financial(price=72.96) → data_engineer 解读` 均携带正确价格。但 `chief_researcher` 在撰写报告时忽略了下文的财务数据，凭训练知识编造了 6.00 元。这是 LLM 的经典幻觉——当上下文数据与训练知识冲突时，模型倾向于信任训练知识。

**修复：**
`chief_researcher.py` system prompt 增加约束：所有数字必须来自提供的财务数据，无数据时标注"数据未提供"而非编造。

**涉及文件：**
- `backend/agent_core/sub_agents/chief_researcher.py`

---

## 21. Agent 无 Tool 选择权 — 工具调用硬编码

**状态：** ✅ 已修复

**现象：** 每个 Agent 的 `execute()` 里直接写死了调哪个工具，比如 `deep_scout` 固定调 `web_search`，`data_engineer` 固定调 `fetch_financial_data`。Agent 无法根据上下文自主决定用哪个工具。

**根因：** 架构设计上 Agent 被当作"固定管道节点"而非自主 Agent——prompt 暗示 + 代码硬绑定工具，不走 function calling。

**修复（已完成）：**
- `BaseAgent` 新增 `_call_llm_with_tools()`：支持 OpenAI 兼容的 `tools`/`tool_choice=auto` 参数，循环执行 LLM 选择的工具调用，`max_rounds=4` 兜底防死循环，同轮工具调用用 `asyncio.gather` 并行执行
- 配套 `_execute_tool_call()` + `_parse_tool_args()`（容错非 JSON 参数）
- `deep_scout` 改为 function calling 模式：LLM 自主决定搜索角度（实测自主发起 4 次搜索 + 1 次知识检索），产出搜索综合结果；FC 失败或无结果时自动降级回原硬编码搜索流程
- 修复连带发现的 `web_search` bug：`import asyncio` 位于 `from ddgs import DDGS` 之后，ddgs 缺失时 `except asyncio.TimeoutError` 抛 UnboundLocalError 掩盖真实错误（asyncio 已移到模块顶部）
- DDGS 搜索超时 5s → 20s（实测 ddgs 首次查询需 ~15s）

**涉及文件：**
- `backend/app/agents/base/base_agent.py`
- `backend/agent_core/sub_agents/deep_scout.py`
- `backend/app/tools/registry.py`

---

## 22. Agent 无多轮迭代能力

**状态：** ✅ 已实现（Supervisor 级多轮追问，见 #26）

**现象：** 每个 Agent 的 `execute()` 跑一次就结束，无法对自己的产出做自我评估和修正。唯一的多轮是 `critic → researcher` 那一条回退边，但也只是重新跑一遍 researcher，不是 Agent 内部的迭代。

**根因：** `BaseAgent.execute()` 设计成单次调用，没有 while 循环/自评机制。`reflect()` 方法是空骨架，从未被调用。

**影响：**
- Agent 生成质量全凭一次 LLM 调用的运气
- 无法像人类分析师那样"写一版 → 审阅 → 修改"
- Critic 评审出问题也只能整段重来，不能局部修正

**修复方向：** `execute()` 改 `run(max_turns)`，内部 while 循环：`_call_llm` → 评估产出 → 不满足则反馈修正 → 继续，直到达标或用完轮次。

**涉及文件：**
- `backend/app/agents/base/base_agent.py`
- `backend/agent_core/sub_agents/*.py`（全部 6 个 Agent）

---

## 23. Agent 无记忆系统

**现象：** 当前 Agent 没有任何记忆：
- 短期：`_call_llm` 每次传独立的 messages，前一轮回复不保留
- 中期：`ResearchTask` 只存最终入/出，不存多轮轨迹
- 长期：跨任务知识沉淀为零——同一个股票第二次跑研报不会比第一次好

**根因：** 设计时未考虑记忆分层。`_call_llm` 仅传当前轮 prompt，`intermediate dict` 只保留最新数据不保留历史轨迹。

**影响：**
- Agent 无法从之前的迭代中学习
- 任务中断后无法恢复上下文
- 跨任务知识不能复用

**修复方向：**
- 短期：`messages[]` 滑动窗口，保留最近 N 轮对话历史
- 中期：`ResearchTask` 加 `conversation_log: JSON`，存完整多轮轨迹，任务可恢复
- 长期：任务结束后提炼 insights → 向量库 → 下次同类任务自动检索

**涉及文件：**
- `backend/app/agents/base/base_agent.py`
- `backend/app/models/database.py`
- `backend/agent_core/scheduler_agent/graph_builder.py`

---

## 24. DAG 编译时固定，Agent 间无法主动对话

**状态：** ✅ 已修复（Supervisor 动态编排，见 #26）

**现象：** Agent 只能通过 `intermediate dict` 被动读写数据，没有能力主动发送消息给另一个 Agent。图结构在 `_build_graph()` 里用 `add_edge` 一次性定死，运行时不能动态增加交互。

**根因：** `StateGraph` 编译时固定边拓扑，Agent 之间是隐式数据流而非显式消息传递。

**影响：**
- Agent A 不能主动找 Agent B 确认信息或请求补充数据
- 无法实现类似 AutoGen 的 Agent 间对话协商
- 添加新的交互关系必须改图代码 + 重新编译

**修复方向：** `GraphState` 加 `inbox: list[Message]` + `send()` 方法，运行时动态路由消息。

**涉及文件：**
- `backend/agent_core/scheduler_agent/graph_builder.py`
- `backend/app/core/state.py`

---

## 25. LLM 后端迁移至本地模型（Qwen2.5-3B-Instruct）

**背景：** 用户要求讨论去掉远程 API 调用，改用本地模型运行全流程。

**实施：**
- 新增 `backend/app/llm/local_qwen.py`：lazy 单例加载（transformers，bf16，`device_map="auto"`），`_gen_lock` 线程锁串行化推理（单 GPU 不能并发），`run_in_executor` 隔离阻塞生成
- function calling 本地版：ReAct 风格 JSON 协议（`{"tool": ...}` / `{"answer": ...}`），不依赖 DeepSeek 的 tools 参数
- `base_agent.py` 的 `_call_llm` / `_call_llm_with_tools` 按 `settings.llm_provider` 分支：`local_qwen` / `deepseek`，可随时切换
- `.env` 新增 `LLM_PROVIDER=local_qwen` + `LOCAL_MODEL_PATH`（指向 modelscope 缓存）

**环境约束与处理：**
- 16GB RAM + 8GB VRAM：7B 模型（D:\qwen，14GB bf16）放不下；改用 3B（5.75GB bf16）直接装 GPU
- bitsandbytes 安装超时失败 → 放弃 4-bit 量化路线，3B 无需量化
- transformers 5.x：`torch_dtype` 已废弃 → 用 `dtype` 参数

**压测（本地模型）：**
- P60（宁德时代）：总耗时 ~458s，6/6 success，报告 3140 字
- P61（完美世界）：总耗时 ~400s，6/6 success，报告 2970 字；6 Agent 全 success 后因机器定时关机（凌晨 1:30）进程被强杀，最终状态由 DB 数据恢复
- 瓶颈：研究员长文本生成（~218s 均值，占总量 ~50%），本地 3B 推理 ~25-35 token/s

**连带修复：**
- `projects.py` 工作流超时 300s → 1200s（本地模型跑不完全流程）
- `README.md` 启动命令修正：实际入口是 `main:app` 而非 `app.main:app`

**涉及文件：**
- `backend/app/llm/local_qwen.py`（新增）
- `backend/app/agents/base/base_agent.py`
- `backend/app/api/projects.py`
- `backend/.env`
- `backend/requirements.txt`（无新增依赖，transformers+torch 已在环境）

---

## 26. 多 Agent 化改造 — Supervisor 主控动态编排 + 多轮追问

**背景：** 用户明确要求"真·多 Agent"（从固定 DAG 流水线升级为 Supervisor 模式），并加多轮追问。选定 LangGraph 官方 Supervisor 模式：新增主控 Agent，现有 6 个 worker Agent 封装为可调用工具，由主控 LLM 在运行时自主决定派遣谁、什么顺序、几轮。

**实施：**
- 新增 `backend/agent_core/sub_agents/supervisor_agent.py`：`SupervisorAgent(BaseAgent)` + `decide()`（provider 分支：本地 Qwen 走 ReAct JSON 协议，DeepSeek 走原生 tools），6 个 worker 工具定义（run_architect / run_scout / run_data_engineer / run_analyst / run_researcher / run_critic）
- `graph_builder.py` 重写：图简化为 `START → parse → supervisor → END`；`_node_supervisor` 内部循环（≤8 轮），每轮把"阶段状态快照"（大纲/搜索/财务/图表/草稿/审查分数反馈）给主控，执行其选中的 worker 后回填状态
- **多轮追问闭环**：run_researcher 执行后自动触发 critic；critic 未通过 → 快照带反馈 → 主控可带 `instruction`（critic 反馈）重派 run_researcher，critic 携带 `critic_previous_feedback` 复查上一轮问题是否修复（追问记忆），迭代直到通过或轮次耗尽
- `chief_researcher` 支持 `research_instruction` 注入；`critic_master` 支持上次反馈复查
- **防线（针对本地 3B 小模型可靠性）**：① 阶段门控—模型决策无效/乱 finish 时自动按规范顺序推进下一缺省阶段；② 重复守卫—同一 worker 连续 ≥2 次强制兜底；③ 兜底流水线—主控完全失效时顺序跑完整流程 + 一轮反馈修订；④ critic 阈值—score≥55 视为通过（3B 评审过严会烧光轮次）
- supervisor 自身结果落库 ResearchTask（rounds / fallback / review 信息）

**踩坑记录：**
1. `WORKER_BY_TOOL` 映射错误（run_scout→scout，实际 agent 是 deep_scout）→ KeyError 后强制兜底；模型决策本身是对的
2. 重复守卫误伤合法追问：researcher→(auto critic)→researcher 被判定"连续重复"→ 修正：auto critic 后重置 last_tool/repeats
3. 3B 首轮直接答 `{"answer": ...}`（finish）不派 worker → 阶段门控兜住

**压测（本地 Qwen2.5-3B，依次修复过程）：**

| 项目 | 结果 | round | fallback | review | 说明 |
|---|---|---|---|---|---|
| P62 比亚迪 | success | — | ✅ | 2 次修订 | 映射 bug 前的老代码，全兜底路径 |
| P63 茅台 | success | — | ✅ | 2 次修订 | 同上 |
| P64 腾讯 | success | 8 | ✅(守卫触发) | 3 次未过 | 模型全自主决策（scout→engineer→architect→analyst→researcher） |
| P65 招行 | success | 8 | ❌ | 4 次未过 | 守卫修复后追问循环跑满，无兜底 |
| P66 隆基绿能 | success | 6 | ❌ | ✅通过(75分) | 阈值生效，1 次评审即收敛，报告 7752 字 |

**结论：** 本地 3B 可实现完整自主编排（自动规划顺序、带反馈重派研究员），配合阶段门控 + 兜底流水线可保证 100% 产出报告；critic 严格度需阈值收敛，否则追问循环会烧满轮次。

**涉及文件：**
- `backend/agent_core/sub_agents/supervisor_agent.py`（新增）
- `backend/agent_core/scheduler_agent/graph_builder.py`
- `backend/agent_core/sub_agents/chief_researcher.py`
- `backend/agent_core/sub_agents/critic_master.py`

---

## 27. DeepSeek API 兜底（调用失败 + 评分不合格双重降级）

**背景：** 本地 Qwen2.5-3B 为主推理后端，但存在两个缺口：① 本地推理崩溃/超时/空输出时流程直接失败；② 本地 critic 评审过严，追问循环烧满轮次也不通过（P64/P65 实测）。用户要求用 DeepSeek API 做兜底。

**实施（两级兜底）：**

| 级别 | 触发条件 | 行为 |
|---|---|---|
| **调用兜底** | 本地 `generate` 异常/空输出（`_call_llm` / `_call_llm_with_tools` / supervisor `decide`） | 自动重试一次 DeepSeek API |
| **评分兜底** | `review_attempts >= 2`（本地评审连续未通过） | 下一次 `run_researcher` + `run_critic` 强制切 DeepSeek 重写再审 |

**实现细节：**
- `BaseAgent._call_llm` / `_call_llm_with_tools` 拆出 `_call_llm_deepseek` / `_call_llm_with_tools_deepseek`，本地分支 try/except + 空输出检查 → DS 兜底（`settings.deepseek_api_key` 存在时）
- `SupervisorAgent.decide` 拆出 `_decide_deepseek`，本地决策异常 → DS
- `_call_llm` 增加 `provider_override` 参数：researcher / critic 通过 intermediate 的 `research_provider` / `critic_provider` 键强制指定 provider（supervisor 升级时写入）
- `graph_builder.py`：`REVIEW_ESCALATE_AFTER = 2`，researcher 派遣前检查 `review_attempts >= 2` → 写 `research_provider=deepseek` + `critic_provider=deepseek`；兜底流水线的修订轮同样强制 DS

**压测（P67 爱尔眼科，本地为主）：**
- round 5：本地 researcher + 本地 critic → 未通过（attempt 1）
- round 6：模型再派 run_critic → 未通过（attempt 2）
- round 7：模型重派 run_researcher → **自动升级 DS**（日志 `escalating researcher+critic to DeepSeek`）→ DS 重写 + DS 评审 → **passed=True（72 分）**
- 最终：8 轮、无兜底流水线、报告 10912 字、status=success

**结论：** 两级兜底配合下，本地小模型"能跑"与"质量达标"可兼得：本地便宜跑通，质量不达标自动升 DS。DS 网络失败时仍走原异常路径，不影响本地主链路。

**涉及文件：**
- `backend/app/agents/base/base_agent.py`
- `backend/agent_core/sub_agents/supervisor_agent.py`
- `backend/agent_core/sub_agents/chief_researcher.py`
- `backend/agent_core/sub_agents/critic_master.py`
- `backend/agent_core/scheduler_agent/graph_builder.py`

---

## 28. 用户侧多轮追问（Q&A 模式）

**背景：** 项目最初只有"创建 → 生成报告 → 查看"，用户无法对已有报告继续提问。多轮追问此前仅为系统内部 critic→researcher 闭环，用户侧"再问一句"能力缺失。

**方案：Q&A 式追问，不重生成报告。**
- 新增 `POST /api/projects/{id}/followup`（`FollowupRequest{question}`），后台线程执行，3600s 超时
- `WorkflowGraph.run_followup`：加载原报告（截取 4000 字）+ 最近 N 轮对话，单次 LLM 调用直接作答（`max_tokens=1536`），不经过 supervisor 循环、不重出整篇报告
- **会话隔离**：每轮追问独立 session，review/决策状态零残留
- **滑动窗口**（不做长期记忆）：`CONV_WINDOW=4`，最近 4 轮 Q+回答摘要（各 400 字）注入主控快照上下文；窗口外自动遗忘
- 每轮回答独立落库为 `followup_N` 任务行（`mode=qa`），前端详情弹窗"多轮追问"区逐轮展示 + 输入框提交，轮询自动刷新

**前端：** `TaskManage.tsx` 详情弹窗增加"多轮追问"区块（历史 Q/A 卡片 + 提交框），`api.ts` 新增 `followupProject()`。

**性能教训（首版踩坑）：** 追问最初复用完整报告流水线 —— researcher 带整份原报告（8000 字）+ 全部中间产物（≈2 万 token 上下文）在本地 3B 上单次生成 15~55 分钟，两次撞 3600s 超时。同时发现 Epic Games Launcher / EOSOverlay 等外部进程抢占 GPU 导致解码骤降。改 Q&A 模式后单轮 1~3 分钟完成。`_call_llm` 由此增加 `max_tokens` 参数（local→max_new_tokens，DS→max_tokens）。

**联调记录（P67 爱尔眼科）：**
- followup_2「分析爱尔眼科未来的分红能力和派息潜力」→ 995 字：引用原报告数据（营收 639 亿 +15%、成本率 97.8%、经营现金流 11.6 亿、每 10 股派 2.5 元、分红比例 31.25%），含理由与风险提示
- followup_3「和通策医疗相比，爱尔眼科的估值是否更有优势？」→ 505 字：直接作答（规模/品牌/财务三要点 + 综述），未重复前轮内容 → 滑动窗口 + 会话隔离生效

**涉及文件：**
- `backend/app/api/projects.py`
- `backend/agent_core/scheduler_agent/graph_builder.py`
- `backend/app/agents/base/base_agent.py`
- `backend/agent_core/sub_agents/chief_researcher.py`
- `backend/app/schemas/common.py`
- `frontend/src/services/api.ts`
- `frontend/src/pages/TaskManage.tsx`
