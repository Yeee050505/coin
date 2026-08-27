# coding: utf-8
"""测试 react_protocol LoRA adapter 输出正确性。"""
import json
import sys
import time
sys.path.insert(0, ".")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

MODEL_PATH = r"C:/Users/tang'ji/.cache/modelscope/models/Qwen--Qwen2.5-3B-Instruct/snapshots/master"
ADAPTER_PATH = "training/lora_output/react_protocol/adapter"

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

TEST_CASES = [
    ("宁德时代(300750)公司概况搜索", "Research topic: 宁德时代(300750). Search for company overview."),
    ("贵州茅台财务数据获取", "Got 贵州茅台 overview. Now fetch income statement data."),
    ("比亚迪资产负债表", "Got 比亚迪 income data. Get balance sheet too."),
    ("招商银行图表生成", "All data collected for 招商银行. Generate a revenue trend chart."),
    ("报告合成输出", "All data collected for 宁德时代. Synthesize the final report."),
    ("搜索失败重试", "web_search returned no results. Try different search query for 贵州茅台."),
    ("美的集团行业分析", "Research 美的集团(000333). Search for: 家电行业出海战略."),
    ("恒瑞医药研发图表", "Create a line chart showing 恒瑞医药 R&D spending over years."),
]


def load_model():
    print("Loading base model...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    print("Loading LoRA adapter...")
    model = PeftModel.from_pretrained(model, ADAPTER_PATH)
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
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def run_tests():
    model, tokenizer = load_model()
    results = []
    correct = 0
    total = len(TEST_CASES)

    for i, (name, user_input) in enumerate(TEST_CASES):
        print(f"\n[{i+1}/{total}] {name}")
        print(f"  Input: {user_input[:60]}...")
        t0 = time.time()
        raw = generate(model, tokenizer, user_input)
        elapsed = time.time() - t0
        parsed = parse_json(raw)

        is_valid = parsed is not None
        has_tool_or_answer = is_valid and ("tool" in parsed or "answer" in parsed)
        has_args = is_valid and "tool" in parsed and isinstance(parsed.get("args"), dict)

        passed = is_valid and has_tool_or_answer
        if "tool" in (parsed or {}):
            passed = passed and has_args

        if passed:
            correct += 1

        status = "PASS" if passed else "FAIL"
        print(f"  Output: {raw[:120]}")
        print(f"  Parsed: {parsed}")
        print(f"  Status: {status} | Time: {elapsed:.2f}s")

        results.append({
            "name": name,
            "input": user_input,
            "raw_output": raw,
            "parsed": parsed,
            "valid_json": is_valid,
            "has_tool_or_answer": has_tool_or_answer,
            "has_args": has_args,
            "passed": passed,
            "time_s": round(elapsed, 2),
        })

    accuracy = correct / total * 100
    print(f"\n{'='*50}")
    print(f"Results: {correct}/{total} passed ({accuracy:.1f}%)")

    with open("training/test_results.json", "w", encoding="utf-8") as f:
        json.dump({"accuracy": accuracy, "passed": correct, "total": total, "results": results}, f, ensure_ascii=False, indent=2)
    print("Results saved to training/test_results.json")
    return results, accuracy


if __name__ == "__main__":
    run_tests()
