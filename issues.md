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

---

## 10. 旧服务器进程未彻底杀死

**现象：** `Stop-Process -Name python` 未能杀掉所有 Python 进程（带控制台的进程杀不掉），端口被旧进程持续占用。

**修复：** 使用 `taskkill /F /PID` 强制杀死指定 PID。

---

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
