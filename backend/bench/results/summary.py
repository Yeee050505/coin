# coding: utf-8
import json, statistics
p = r"D:\py\fastapi_demo\coin\backend\bench\results\bench_results.json"
d = json.load(open(p, encoding="utf-8"))
modes = {}
for r in d["runs"]:
    modes.setdefault(r["mode"], []).append(r)
lines = ["| 模式 | 三次耗时(s) | 平均(s) | 中文字数(三次) | 平均 | 成功率 | 兜底 | OOM事件 |", "|---|---|---|---|---|---|---|---|"]
for m in ["deepseek", "auto", "local_qwen"]:
    rs = modes[m]
    secs = [x["seconds"] for x in rs]
    cjk = [x["cjk_chars"] for x in rs]
    fb = sum(x["log_stats"]["fallback"] for x in rs)
    ok = sum(1 for x in rs if x["status"] == "success")
    oom = 3 if m == "local_qwen" else 0
    lines.append(f"| {m} | {'/'.join(str(s) for s in secs)} | {statistics.mean(secs):.1f} | {'/'.join(str(c) for c in cjk)} | {statistics.mean(cjk):.0f} | {ok}/3 | {fb} | {oom} |")
print("\n".join(lines))
print()
print("router(auto):", d["mode_stats"]["auto"]["router"])
print("mode stats:", json.dumps(d["mode_stats"], ensure_ascii=False))
print("commit:", d.get("commit"))
