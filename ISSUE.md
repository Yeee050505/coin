# 修复：工作流执行过程中因 MySQL JSON 序列化异常导致流程挂死 & 性能优化

## 根因分析

工作流执行到第 2 个 Agent（`deep_scout`）后整体挂死，后续 Agent 无法继续。

直接原因：MySQL JSON 列（`ResearchTask.output_data`）无法序列化 numpy 类型（`numpy.int64`、`numpy.float64`、`numpy.datetime64`）、`float nan` / `float inf` 以及 pandas Timestamp。Agent 输出的中间结果中包含这些类型时，`json.dumps` → `ORM` commit 会抛出 `TypeError` / `ValueError` 异常。

连锁效应：`graph_builder.py` 中的 `_save_task()` 没有 try/except commit，异常向上传播导致整个 `run()` 协程崩溃；`projects.py` 中 `run_workflow_sync()` 的异常处理路径使用了已失效的 `local_db` session（被前序操作污染），`status_db = get_session()` 获得新 session 后仍需 commit 才能持久化状态变更，最终状态停留在 `"running"`，前端轮询永不结束，造成"挂死"假象。

## 修复内容

### 1. `_json_safe()` 安全序列化（`graph_builder.py:21-51`）

全面处理不可 JSON 序列化的类型：

- numpy 标量（`numpy.int64/float64` 等）→ 调用 `.item()` 递归转换
- `numpy.datetime64` → `.item()` 转 Python datetime → `.isoformat()`
- `float('nan')` / `float('inf')` → `None`
- pandas Timestamp → `.to_pydatetime().isoformat()`
- 兜底逻辑：`int()` / `float()` / `str()` 逐级尝试

### 2. `_save_task()` 事务安全（`graph_builder.py:82-86`）

添加 try/except commit，失败时执行 `db_session.rollback()` 后重新 raise，防止脏 session 泄漏：

```python
try:
    db_session.commit()
except Exception:
    db_session.rollback()
    raise
```

### 3. `projects.py` 使用独立 session 更新状态（`projects.py:41-78`）

成功/超时/异常三种路径统一使用**新创建的** `get_session()`（而非 `local_db`）更新 `ResearchProject` 状态：

- 成功后：`status_db = get_session()` → `p.status = "success"` → 写报告到磁盘 → `commit()` → `close()`
- 超时/异常：`err_db = get_session()` → `p.status = "failed"` → `commit()` → `close()`
- `local_db` 仅用于 workflow 内部的 task 写入，生命周期与 workflow 绑定

### 4. 移除冗余的最终 re-save 循环

最后一个 Agent `critic_master` 执行完后不再对整个 agent_tasks 做全量 re-save（原有逻辑显式重复保存，已删除），减少不必要的 DB 写入。

## 性能优化

### 1. 并行 LLM 调用（`graph_builder.py:107`）

前三个 Agent（`chief_architect`、`deep_scout`、`chief_data_engineer`）通过 `asyncio.gather` 并发执行：

```python
await asyncio.gather(*[self.agents[n].run(state) for n in parallel_group1])
```

- 优化前（顺序执行）：~45 秒
- 优化后（并发执行）：~17 秒
- 加速比：~2.6x

### 2. Prompt 压缩（`data_engineer.py:27-52` `_compact_financial()`）

原始 `fetch_financial_data` 返回的 API 数据约 97K 字符（详尽的 JSON 嵌套结构），压缩为仅包含关键指标的字符串：

- 提取 `price`、`volume`、`history` 的起止收盘价、最高/最低
- 提取 `data` 数组的记录数和前 2 条示例
- `nan`/`None` 值剔除
- 最终长度限制在 900 字符以内

### 3. Web 搜索并行化（`deep_scout.py:28`）

三个搜索查询（通用搜索、行业数据、最新动态）通过 `asyncio.gather` 并行发起：

```python
results = await asyncio.gather(*[call_tool("web_search", query=q, max_results=3) for q in queries], return_exceptions=True)
```

- 优化前：3 次串行搜索（~3× 单次延迟）
- 优化后：3 次并行搜索（~1× 单次延迟）

## 时间线

| 阶段 | 耗时 | 说明 |
|---|---|---|
| 并行组（3 Agent） | ~17s | `chief_architect` + `deep_scout` + `chief_data_engineer` |
| `data_analyst` | ~6s | 单 Agent 分析 |
| `chief_researcher` + `critic_master` | ~35s | 含可能的重试（review 失败回退） |
| **总计** | **~60s** | Wall time，P41 验证通过 |

## P41 验证结果

- 全部 6 个 Agent 状态为 `success`
- `ResearchTask.output_data` 成功写入 MySQL JSON 列，无序列化异常
- `ResearchProject.status = "success"`，`report_path` 指向写入磁盘的最终报告
- 前端轮询正常结束，显示完成状态

## 涉及文件

| 文件 | 变更 |
|---|---|
| `backend/agent_core/scheduler_agent/graph_builder.py` | `_json_safe`、`_save_task` 事务安全、并行 gather、移除冗余 re-save |
| `backend/app/api/projects.py` | 成功/失败路径使用 `get_session()` 独立 session |
| `backend/agent_core/sub_agents/deep_scout.py` | Web 搜索 `asyncio.gather` 并行 |
| `backend/agent_core/sub_agents/data_engineer.py` | `_compact_financial` prompt 压缩 |
