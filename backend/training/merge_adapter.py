# coding: utf-8
"""合并 LoRA adapter 到基础模型，输出完整模型。

用法:
    cd backend
    python -m training.merge_adapter --direction report_writing
    python -m training.merge_adapter --direction all
"""

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def merge_single(direction: str, model_path: str, adapter_dir: str, output_dir: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    adapter_path = os.path.join(adapter_dir, direction, "adapter")
    if not os.path.exists(adapter_path):
        logger.error(f"Adapter not found: {adapter_path}")
        return None

    logger.info(f"=== Merging LoRA: {direction} ===")
    logger.info(f"Base model: {model_path}")
    logger.info(f"Adapter: {adapter_path}")

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    base_model = AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )

    model = PeftModel.from_pretrained(base_model, adapter_path)
    model = model.merge_and_unload()

    direction_output = os.path.join(output_dir, direction)
    os.makedirs(direction_output, exist_ok=True)

    model.save_pretrained(direction_output)
    tokenizer.save_pretrained(direction_output)

    logger.info(f"Merged model saved to {direction_output}")
    return direction_output


def main():
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument("--direction", default="report_writing",
                        choices=["react_protocol", "report_writing", "critic_review", "supervisor_dispatch", "all"])
    parser.add_argument("--model-path", default="", help="Path to base model")
    parser.add_argument("--adapter-dir", default=str(Path(__file__).resolve().parent / "lora_output"))
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "merged_models"))
    args = parser.parse_args()

    from app.core.config import settings
    model_path = args.model_path or settings.local_model_path
    if not model_path:
        logger.error("No model path. Set LOCAL_MODEL_PATH or use --model-path")
        sys.exit(1)

    directions = ["react_protocol", "report_writing", "critic_review", "supervisor_dispatch"] if args.direction == "all" else [args.direction]

    for d in directions:
        merge_single(d, model_path, args.adapter_dir, args.output_dir)

    logger.info("All merges complete")


if __name__ == "__main__":
    main()
