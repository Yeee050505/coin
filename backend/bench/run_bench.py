# coding: utf-8
"""三模式压测编排: deepseek / auto / local_qwen x 3只标的
用法: python run_bench.py
切换 backend/.env 的 LLM_PROVIDER 并重启服务, 串行提交任务, 采集指标,
结果写入 bench/results/progress.json (过程) 与 bench/results/bench_results.json (最终)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
ENV_PATH = os.path.join(ROOT, ".env")
APPLOG = os.path.join(ROOT, "logs", "app.log")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
PROGRESS = os.path.join(OUT_DIR, "progress.json")
RESULTS = os.path.join(OUT_DIR, "bench_results.json")
CONSOLE = os.path.join(OUT_DIR, "bench_console.log")

BASE = "http://127.0.0.1:8001"
PY = r"C:\conda\python.exe"

STOCKS = [
    ("贵州茅台", "600519"),
    ("比亚迪", "002594"),
    ("宁德时代", "300750"),
]
MODES = [
    ("deepseek", 600),
    ("auto", 600),
    ("local_qwen", 1200),
]
CJK = re.compile(r"[\u4e00-\u9fff]")


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(CONSOLE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_progress(obj):
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROGRESS)


def set_provider(mode):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    if re.search(r"^LLM_PROVIDER=.*$", text, re.M):
        text = re.sub(r"^LLM_PROVIDER=.*$", f"LLM_PROVIDER={mode}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nLLM_PROVIDER={mode}\n"
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def kill_servers():
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object { $_.CommandLine -match 'uvicorn|backend[\\\\/].*main\\.py' -and $_.CommandLine -notmatch 'run_bench' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-Command", ps], capture_output=True, timeout=30)
    time.sleep(3)


def start_server():
    out_path = os.path.join(OUT_DIR, "server_stdout.log")
    out = open(out_path, "ab")
    subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"],
        cwd=ROOT, stdout=out, stderr=out,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200),
    )
    out.close()


def healthy(timeout=2):
    try:
        with urllib.request.urlopen(BASE + "/docs", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_healthy(seconds=40):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if healthy():
            return True
        time.sleep(2)
    return False


def restart_server():
    kill_servers()
    start_server()
    return wait_healthy()


def http_json(method, path, body=None, timeout=30):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def log_offset():
    try:
        return os.path.getsize(APPLOG)
    except OSError:
        return 0


def read_log_new(offset):
    try:
        with open(APPLOG, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def count_patterns(text):
    fallback = len(re.findall(r"falling back to DeepSeek", text))
    router = {}
    for m in re.finditer(r"\[router\]\s+\S+\s+->\s+(\w+)", text):
        router[m.group(1)] = router.get(m.group(1), 0) + 1
    oom = len(re.findall(r"CUDA|out of memory|0xc0000005", text, re.I))
    errors = len(re.findall(r"\bERROR\b", text))
    return {"fallback": fallback, "router": router, "oom_or_crash": oom, "errors": errors}


def report_chars(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return len(CJK.findall(text)), len(text)
    except OSError:
        return None, None


def run_project(name, code, timeout_s):
    title = f"{name} {code} 投资价值分析"
    desc = f"从基本面、资金流、估值、行业、研报、新闻等多角度分析{name} {code} 投资价值"
    t0 = time.time()
    resp = http_json("POST", "/api/projects",
                     {"title": title, "description": desc, "scenario": "financial_report"})
    pid = resp.get("project_id")
    log(f"  project {pid} ({name}) submitted")
    off = log_offset()
    status = None
    last_healthy = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(8)
        if not healthy():
            if time.time() - last_healthy < 45:
                continue
            log(f"  project {pid}: server DOWN, abort wait")
            return {"project_id": pid, "status": "server_down", "seconds": round(time.time() - t0, 1),
                    "log_stats": count_patterns(read_log_new(off))}
        last_healthy = time.time()
        try:
            d = http_json("GET", f"/api/projects/{pid}", timeout=15)
        except Exception:
            continue
        status = d.get("project", {}).get("status")
        if status in ("success", "failed", "completed_with_issues"):
            seconds = round(time.time() - t0, 1)
            rpath = d.get("project", {}).get("report_path") or ""
            cjk, total = report_chars(rpath)
            tasks = d.get("tasks") or []
            agent_durations = {}
            for t in tasks:
                if t.get("started_at") and t.get("completed_at"):
                    try:
                        s = datetime.fromisoformat(t["started_at"])
                        e = datetime.fromisoformat(t["completed_at"])
                        agent_durations[t["agent_name"]] = round((e - s).total_seconds(), 1)
                    except Exception:
                        pass
            log(f"  project {pid}: {status} in {seconds}s, cjk={cjk}")
            return {"project_id": pid, "status": status, "seconds": seconds,
                    "cjk_chars": cjk, "total_chars": total, "agent_durations": agent_durations,
                    "log_stats": count_patterns(read_log_new(off))}
    log(f"  project {pid}: TIMEOUT after {timeout_s}s")
    return {"project_id": pid, "status": "timeout", "seconds": round(time.time() - t0, 1),
            "log_stats": count_patterns(read_log_new(off))}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(ROOT),
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        commit = "?"
    all_results = {"started_at": datetime.now().isoformat(), "commit": commit, "runs": []}
    progress = {"phase": "init", "current_mode": None, "completed": [], "results_file": RESULTS}
    write_progress(progress)

    for mode, timeout_s in MODES:
        log(f"=== MODE {mode}: switching provider, restarting server ===")
        set_provider(mode)
        if not restart_server():
            log(f"FATAL: server failed to start for mode {mode}")
            progress["phase"] = f"fatal:{mode}"
            write_progress(progress)
            return 2
        time.sleep(2)
        mode_off = log_offset()
        log(f"=== MODE {mode}: server up, starting runs ===")
        progress.update({"phase": f"running:{mode}", "current_mode": mode})
        write_progress(progress)

        mode_runs = []
        for name, code in STOCKS:
            r = run_project(name, code, timeout_s)
            r.update({"mode": mode, "stock": f"{name} {code}"})
            # 失败/宕机重试一次(仅该项目)
            if r["status"] in ("failed", "server_down", "timeout"):
                log(f"  retry {name} for mode {mode} ...")
                if not healthy() and not restart_server():
                    r["retry"] = "server_unrecoverable"
                    mode_runs.append(r)
                    progress["completed"].append(r)
                    write_progress(progress)
                    continue
                time.sleep(3)
                r2 = run_project(name, code, timeout_s)
                r2.update({"mode": mode, "stock": f"{name} {code}", "retry_of": r["project_id"]})
                r = r2
            mode_runs.append(r)
            progress["completed"].append(r)
            write_progress(progress)
            time.sleep(3)

        mode_stats = count_patterns(read_log_new(mode_off))
        # 模式级 router 校验
        if mode == "auto" and mode_stats["router"].get("local_qwen", 0) + mode_stats["router"].get("deepseek", 0) == 0:
            log(f"WARN: auto mode has no [router] lines!")
        if mode != "auto" and any(mode_stats["router"].values()):
            log(f"WARN: mode {mode} unexpectedly has [router] lines: {mode_stats['router']}")
        all_results["runs"].extend(mode_runs)
        all_results.setdefault("mode_stats", {})[mode] = mode_stats
        log(f"=== MODE {mode} done: {mode_stats} ===")
        with open(RESULTS, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=1)

    # 恢复 auto 并重启留作日常服务
    log("restoring LLM_PROVIDER=auto and restarting server ...")
    set_provider("auto")
    restart_server()
    all_results["finished_at"] = datetime.now().isoformat()
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    progress.update({"phase": "done", "current_mode": None})
    write_progress(progress)
    log("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
