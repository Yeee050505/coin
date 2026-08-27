# coding: utf-8
"""基座模型 baseline 测试 — 不加载 adapter，建立训练前基线。"""
import json
import sys
import time
import re
sys.path.insert(0, ".")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = r"C:/Users/tang'ji/.cache/modelscope/models/Qwen--Qwen2.5-3B-Instruct/snapshots/master"

SYSTEM_PROMPT = (
    "You are a financial research assistant. Use tools to gather data.\n\n"
    "Tools available:\n"
    "- web_search: Search web for information args: query: str, max_results: int\n"
    "- fetch_financial_data: Fetch stock financial data args: stock_code: str, indicator: str, years: int\n"
    "- run_chart_code: Generate chart args: chart_type: str, title: str, labels: list, values: list\n\n"
    'When a tool is needed, reply with ONLY: {"tool": "<name>", "args": {...}}\n'
    'When the final answer is ready, reply with ONLY: {"answer": "<text>"}\n'
    "Never output anything other than that JSON."
)

# ============================================================
# 测试用例分 5 类，共 42 条
# ============================================================

TEST_CASES = [
    # ── A. 标准正常 case（10 条）─────────────────────────────
    ("A01 宁德时代概况搜索", "Research topic: 宁德时代(300750). Search for company overview."),
    ("A02 贵州茅台利润表", "Got 贵州茅台 overview. Now fetch income statement data."),
    ("A03 比亚迪资产负债表", "Got 比亚迪 income data. Get balance sheet too."),
    ("A04 招商银行营收趋势图", "All data collected for 招商银行. Generate a revenue trend chart."),
    ("A05 报告合成输出", "All data collected for 宁德时代. Synthesize the final report."),
    ("A06 搜索失败重试", "web_search returned no results. Try different search query for 贵州茅台."),
    ("A07 美的集团行业分析", "Research 美的集团(000333). Search for: 家电行业出海战略."),
    ("A08 恒瑞医药研发图表", "Create a line chart showing 恒瑞医药 R&D spending over years."),
    ("A09 中国平安综合金融", "Research 中国平安(601318). Search for: 综合金融布局与科技战略."),
    ("A10 隆基绿能现金流", "Got 隆基绿能 overview. Fetch cashflow statement data for the last 5 years."),

    # ── B. 边界 case — 模糊/缺省输入（10 条）────────────────
    ("B01 无股票代码模糊搜索", "帮我查一下最近白酒行业的情况"),
    ("B02 只有公司名无代码", "比亚迪最近的财务数据怎么样"),
    ("B03 口语化搜索", "那个做电池的宁德时代 营收多少来着"),
    ("B04 英文混合中文", "帮我查一下 贵州茅台 Kweichow Moutai 的收入情况"),
    ("B05 极简输入", "搜一下腾讯"),
    ("B06 无明确指令", "关于隆基绿能 你怎么看"),
    ("B07 只给行业不给公司", "新能源汽车电池行业目前竞争格局如何"),
    ("B08 模糊图表需求", "画个图看看比亚迪的走势"),
    ("B09 模糊财务需求", "把招商银行近几年的数据拉出来看看"),
    ("B10 多公司对比模糊", "比较一下宁德时代和比亚迪"),

    # ── C. 边界 case — 多工具连续调用 / 复杂链路（8 条）────
    ("C01 搜索+财务+图表全链路", "Research 宁德时代. First search overview, then fetch income data, finally generate a revenue bar chart."),
    ("C02 财务+图表", "Fetch 比亚迪 income statement and create a line chart of revenue trend."),
    ("C03 搜索+搜索+综合", "Search for 贵州茅台 company overview and also search for 白酒行业 market size. Then synthesize findings."),
    ("C04 财务多指标", "Fetch 宁德时代 balance sheet and also cashflow statement."),
    ("C05 图表多维度", "Create a pie chart showing 招商银行 revenue composition by business segment."),
    ("C06 搜索+财务", "Search for 比亚迪 overseas expansion strategy and fetch their international revenue data."),
    ("C07 财务+解读", "Fetch 中国平安 income data and provide analysis of profitability trends."),
    ("C08 搜索+图表", "Search for 隆基绿能 solar panel market share and create a bar chart of industry rankings."),

    # ── D. 异常 / 噪声 case（8 条）────────────────────────
    ("D01 输入夹杂乱码", "查找 X#@$% 宁德时代 的公司概况信息"),
    ("D02 多余无关文字", "今天天气不错，对了帮我查一下贵州茅台的财务数据，晚上吃什么"),
    ("D03 重复啰嗦输入", "查一下查一下查一下比亚迪比亚迪比亚迪的财务数据财务数据"),
    ("D04 中英文混杂噪声", "help me search 比亚迪 electric vehicle market position in China market"),
    ("D05 含特殊字符", "【紧急】!! 宁德时代(300750)!!! 最新财务报表!!!"),
    ("D06 指令矛盾", "不要搜索也不要查财务 直接告诉我宁德时代的情况"),
    ("D07 超长无关前缀", "我是一个金融分析师 已经工作了15年 专注于消费电子和新能源赛道 对锂电池产业链有深入研究 现在我想了解一下宁德时代的基本情况"),
    ("D08 纯噪声无意义", "asdfghjkl 1234567890 !@#$%^&*()"),

    # ── E. 边界 case — 特殊工具参数（6 条）────────────────
    ("E01 饼图生成", "Create a pie chart showing 宁德时代 revenue breakdown by product segment: 动力电池 65%, 储能 20%, 电池材料 15%."),
    ("E02 折线图多年数据", "Generate a line chart of 贵州茅台 net profit from 2019 to 2024 with values: 412, 467, 525, 627, 747, 862."),
    ("E03 柱状图对比", "Create a bar chart comparing market cap of 宁德时代, 比亚迪, and 中国平安: 8500, 7200, 4500 (unit: 亿)."),
    ("E04 指定年份范围", "Fetch 贵州茅台 income statement for the last 5 years."),
    ("E05 overview 指标", "Fetch 宁德时代 company overview data."),
    ("E06 cashflow 指标", "Fetch 比亚迪 cashflow statement data for the last 3 years."),
]


def load_model():
    print("Loading BASE model (no adapter)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    model.eval()
    print(f"Model loaded. Device: {model.device}")
    return model, tokenizer


def generate(model, tokenizer, user_input, max_new_tokens=256):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False,
            temperature=1.0, top_p=1.0, pad_token_id=tokenizer.eos_token_id,
        )
    new_tokens = out[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


VALID_TOOLS = {"web_search", "fetch_financial_data", "run_chart_code"}
VALID_INDICATORS = {"income", "balance", "cashflow", "overview"}
VALID_CHART_TYPES = {"bar", "line", "pie"}


def evaluate(name, raw, parsed):
    """返回 (passed, details_dict)"""
    is_valid = parsed is not None
    has_tool_or_answer = is_valid and ("tool" in parsed or "answer" in parsed)
    has_args = is_valid and "tool" in parsed and isinstance(parsed.get("args"), dict)

    # 工具名正确性
    tool_ok = False
    if "tool" in (parsed or {}):
        tool_ok = parsed["tool"] in VALID_TOOLS

    # 参数正确性
    args_ok = False
    if tool_ok and "args" in parsed:
        a = parsed["args"]
        t = parsed["tool"]
        if t == "web_search":
            args_ok = isinstance(a.get("query"), str) and len(a["query"]) > 0
        elif t == "fetch_financial_data":
            args_ok = (
                isinstance(a.get("stock_code"), str) and len(a["stock_code"]) == 6
                and a.get("indicator") in VALID_INDICATORS
            )
        elif t == "run_chart_code":
            args_ok = (
                a.get("chart_type") in VALID_CHART_TYPES
                and isinstance(a.get("labels"), list) and len(a["labels"]) > 0
                and isinstance(a.get("values"), list) and len(a["values"]) > 0
            )

    # answer 类型
    answer_ok = False
    if "answer" in (parsed or {}):
        answer_ok = isinstance(parsed["answer"], str) and len(parsed["answer"]) > 10

    passed = is_valid and has_tool_or_answer
    if "tool" in (parsed or {}):
        passed = passed and has_args and tool_ok and args_ok
    elif "answer" in (parsed or {}):
        passed = passed and answer_ok

    details = {
        "valid_json": is_valid,
        "has_tool_or_answer": has_tool_or_answer,
        "has_args": has_args,
        "tool_valid": tool_ok,
        "args_valid": args_ok,
        "answer_valid": answer_ok,
        "passed": passed,
    }
    return passed, details


def run_tests():
    model, tokenizer = load_model()
    results = []
    correct = 0
    total = len(TEST_CASES)

    cat_stats = {}  # category -> {total, passed}

    for i, (name, user_input) in enumerate(TEST_CASES):
        cat = name[:2]  # A01 -> "A0"
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "passed": 0}
        cat_stats[cat]["total"] += 1

        print(f"\n[{i+1}/{total}] {name}")
        print(f"  Input: {user_input[:80]}")
        t0 = time.time()
        raw = generate(model, tokenizer, user_input)
        elapsed = time.time() - t0
        parsed = parse_json(raw)
        passed, details = evaluate(name, raw, parsed)

        if passed:
            correct += 1
            cat_stats[cat]["passed"] += 1

        status = "PASS" if passed else "FAIL"
        print(f"  Output: {raw[:150]}")
        print(f"  Status: {status} | Time: {elapsed:.2f}s | json={details['valid_json']} tool={details.get('tool_valid','-')} args={details.get('args_valid','-')}")

        results.append({
            "name": name,
            "category": cat,
            "input": user_input,
            "raw_output": raw,
            "parsed": parsed,
            "time_s": round(elapsed, 2),
            **details,
        })

    accuracy = correct / total * 100

    # 汇总
    print(f"\n{'='*60}")
    print(f"总结果: {correct}/{total} passed ({accuracy:.1f}%)")
    print(f"\n分类统计:")
    for cat in sorted(cat_stats.keys()):
        s = cat_stats[cat]
        pct = s["passed"] / s["total"] * 100 if s["total"] else 0
        label = {"A": "标准正常", "B": "模糊/缺省", "C": "多工具链", "D": "异常噪声", "E": "特殊参数"}.get(cat, cat)
        print(f"  {cat} {label}: {s['passed']}/{s['total']} ({pct:.0f}%)")

    # 保存
    report = {
        "accuracy": accuracy,
        "passed": correct,
        "total": total,
        "category_stats": {k: v for k, v in cat_stats.items()},
        "results": results,
    }
    with open("training/test_baseline_results.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to training/test_baseline_results.json")
    return results, accuracy


if __name__ == "__main__":
    run_tests()
