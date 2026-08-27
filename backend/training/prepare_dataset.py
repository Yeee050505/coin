# coding: utf-8
"""将 collect_data.py 产出的 JSONL 转换为 SFT 训练格式 (Alpaca + chat template)。

用法:
    cd backend
    python -m training.prepare_dataset --direction report_writing
    python -m training.prepare_dataset --direction all
    python -m training.prepare_dataset --direction react_protocol --augment --augment-count 5
"""

import argparse
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STOCK_NAMES = [
    "完美世界", "贵州茅台", "五粮液", "宁德时代", "比亚迪",
    "中国平安", "招商银行", "格力电器", "美的集团", "恒瑞医药",
    "药明康德", "隆基绿能", "海康威视", "京东方A", "中兴通讯",
]
STOCK_CODES = [
    "002624", "600519", "000858", "300750", "002594",
    "601318", "600036", "000651", "000333", "600276",
    "603259", "601012", "002415", "000725", "000063",
]
INDICATORS = ["overview", "income", "balance"]
RESEARCH_DIMENSIONS = ["market_size", "financial", "competitor", "risk", "valuation"]


def load_jsonl(path: str) -> List[Dict]:
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def augment_react_samples(samples: List[Dict], count: int = 5) -> List[Dict]:
    """通过随机替换股票名/代码/指标生成更多 ReAct 样本。"""
    augmented = []
    for _ in range(count):
        for s in samples:
            idx = random.randint(0, len(STOCK_NAMES) - 1)
            new_sample = dict(s)
            new_sample["input"] = s["input"].replace("宁德时代", STOCK_NAMES[idx]).replace("300750", STOCK_CODES[idx])
            new_sample["output"] = s["output"].replace("300750", STOCK_CODES[idx])
            augmented.append(new_sample)
    random.shuffle(augmented)
    return samples + augmented


def to_alpaca_format(samples: List[Dict]) -> List[Dict]:
    """转换为标准 Alpaca 格式。"""
    return [
        {
            "instruction": s.get("system", ""),
            "input": s.get("input", ""),
            "output": s.get("output", ""),
        }
        for s in samples
    ]


def to_chat_format(samples: List[Dict]) -> List[Dict]:
    """转换为 Qwen2.5 chat template 格式 (messages list)。"""
    formatted = []
    for s in samples:
        messages = []
        system = s.get("system", "")
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": s.get("input", "")})
        messages.append({"role": "assistant", "content": s.get("output", "")})
        formatted.append({"messages": messages})
    return formatted


def to_sharegpt_format(samples: List[Dict]) -> List[Dict]:
    """转换为 ShareGPT 格式 (SFTTrainer 原生支持)。"""
    formatted = []
    for s in samples:
        conversations = []
        system = s.get("system", "")
        if system:
            conversations.append({"from": "system", "value": system})
        conversations.append({"from": "human", "value": s.get("input", "")})
        conversations.append({"from": "gpt", "value": s.get("output", "")})
        formatted.append({"conversations": conversations})
    return formatted


def split_dataset(samples: List[Dict], train_ratio: float = 0.9) -> tuple:
    """分割训练集和验证集。"""
    random.shuffle(samples)
    split_idx = int(len(samples) * train_ratio)
    return samples[:split_idx], samples[split_idx:]


def main():
    parser = argparse.ArgumentParser(description="Prepare SFT training dataset")
    parser.add_argument("--direction", default="all",
                        choices=["all", "react_protocol", "report_writing", "critic_review", "supervisor_dispatch"])
    parser.add_argument("--input-dir", default=str(Path(__file__).resolve().parent / "data"))
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "dataset"))
    parser.add_argument("--format", default="chat", choices=["alpaca", "chat", "sharegpt"])
    parser.add_argument("--augment", action="store_true", help="Augment react_protocol data")
    parser.add_argument("--augment-count", type=int, default=5)
    parser.add_argument("--train-ratio", type=float, default=0.9)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    directions = ["react_protocol", "report_writing", "critic_review", "supervisor_dispatch"] if args.direction == "all" else [args.direction]

    all_train, all_val = [], []

    for d in directions:
        jsonl_path = os.path.join(args.input_dir, f"{d}.jsonl")
        if not os.path.exists(jsonl_path):
            logger.warning(f"Skipping {d}: {jsonl_path} not found")
            continue

        samples = load_jsonl(jsonl_path)
        logger.info(f"{d}: loaded {len(samples)} samples")

        if d == "react_protocol" and args.augment:
            samples = augment_react_samples(samples, args.augment_count)
            logger.info(f"{d}: augmented to {len(samples)} samples")

        train, val = split_dataset(samples, args.train_ratio)

        if args.format == "alpaca":
            train_formatted = to_alpaca_format(train)
            val_formatted = to_alpaca_format(val)
        elif args.format == "chat":
            train_formatted = to_chat_format(train)
            val_formatted = to_chat_format(val)
        else:
            train_formatted = to_sharegpt_format(train)
            val_formatted = to_sharegpt_format(val)

        all_train.extend(train_formatted)
        all_val.extend(val_formatted)

    random.shuffle(all_train)
    random.shuffle(all_val)

    for split_name, data in [("train", all_train), ("val", all_val)]:
        path = os.path.join(args.output_dir, f"{split_name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info(f"Saved {len(data)} {split_name} samples to {path}")

    logger.info(f"Dataset ready: {len(all_train)} train, {len(all_val)} val")


if __name__ == "__main__":
    main()
