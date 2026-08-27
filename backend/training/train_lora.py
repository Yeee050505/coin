# coding: utf-8
"""LoRA 训练脚本 — 基于 PEFT + TRL SFTTrainer。

支持 4 个训练方向，每个方向独立训练 LoRA adapter：
1. react_protocol   — ReAct JSON 协议
2. report_writing   — 研报撰写
3. critic_review    — Critic 评审
4. supervisor_dispatch — Supervisor 决策

用法:
    cd backend
    python -m training.train_lora --direction report_writing
    python -m training.train_lora --direction react_protocol --epochs 4
    python -m training.train_lora --direction all
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_config():
    from app.core.config import settings
    return settings


def get_lora_config(direction: str):
    """根据训练方向返回 LoRA 配置。"""
    from peft import LoraConfig

    common = dict(
        r=16,
        lora_alpha=32,
        lora_dropout=0.1,
        bias="none",
        task_type="CAUSAL_LM",
    )

    configs = {
        "react_protocol": {
            **common,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
            "r": 8,
            "lora_alpha": 16,
        },
        "report_writing": {
            **common,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        },
        "critic_review": {
            **common,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
            "r": 8,
            "lora_alpha": 16,
        },
        "supervisor_dispatch": {
            **common,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
            "r": 8,
            "lora_alpha": 16,
        },
    }

    return LoraConfig(**configs.get(direction, configs["report_writing"]))


def load_dataset_from_jsonl(path: str, tokenizer, max_seq_length: int = 2048):
    """从 JSONL 加载数据集，直接 tokenize 为 input_ids。"""
    from datasets import Dataset

    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))

    if not samples:
        raise ValueError(f"No samples found in {path}")

    def format_and_tokenize(sample):
        messages = sample.get("messages", [])
        if not messages:
            system = sample.get("instruction", "")
            user = sample.get("input", "")
            assistant = sample.get("output", "")
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": assistant})

        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        tokenized = tokenizer(
            text,
            truncation=True,
            max_length=max_seq_length,
            padding="max_length",
            return_tensors=None,
        )
        tokenized["labels"] = tokenized["input_ids"].copy()
        return tokenized

    dataset = Dataset.from_list(samples)
    dataset = dataset.map(format_and_tokenize, remove_columns=dataset.column_names)
    return dataset


def train(
    direction: str,
    model_path: str,
    dataset_dir: str,
    output_dir: str,
    epochs: int = 3,
    batch_size: int = 2,
    gradient_accumulation: int = 8,
    learning_rate: float = 2e-4,
    max_seq_length: int = 2048,
    warmup_ratio: float = 0.1,
    logging_steps: int = 10,
    save_steps: int = 100,
    eval_steps: int = 50,
):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
    from peft import LoraConfig, get_peft_model, TaskType

    logger.info(f"=== Training LoRA: {direction} ===")
    logger.info(f"Model: {model_path}")
    logger.info(f"Dataset: {dataset_dir}")

    use_cuda = torch.cuda.is_available()
    use_fp16 = use_cuda and torch.cuda.get_device_capability()[0] < 8  # < Ampere
    use_bf16 = use_cuda and torch.cuda.get_device_capability()[0] >= 8  # >= Ampere
    logger.info(f"CUDA: {use_cuda}, fp16: {use_fp16}, bf16: {use_bf16}")

    logger.info("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading model...")
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.bfloat16 if use_bf16 else torch.float16,
        trust_remote_code=True,
    )
    model.config.use_cache = False
    if use_cuda:
        model = model.to("cuda")

    lora_config = get_lora_config(direction)
    logger.info(f"LoRA config: r={lora_config.r}, alpha={lora_config.lora_alpha}, "
                f"target_modules={lora_config.target_modules}")

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_path = os.path.join(dataset_dir, "train.jsonl")
    val_path = os.path.join(dataset_dir, "val.jsonl")

    logger.info("Loading training dataset...")
    train_dataset = load_dataset_from_jsonl(train_path, tokenizer, max_seq_length)
    logger.info(f"Train dataset: {len(train_dataset)} samples")

    eval_dataset = None
    if os.path.exists(val_path):
        eval_dataset = load_dataset_from_jsonl(val_path, tokenizer, max_seq_length)
        logger.info(f"Eval dataset: {len(eval_dataset)} samples")

    direction_output = os.path.join(output_dir, direction)
    os.makedirs(direction_output, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=direction_output,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation,
        learning_rate=learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=warmup_ratio,
        logging_steps=logging_steps,
        eval_strategy="steps" if eval_dataset else "no",
        save_strategy="steps" if eval_dataset else "no",
        eval_steps=eval_steps if eval_dataset else None,
        save_steps=eval_steps if eval_dataset else save_steps,
        save_total_limit=3,
        load_best_model_at_end=True if eval_dataset else False,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        bf16=use_bf16,
        fp16=use_fp16,
        report_to="none",
        remove_unused_columns=False,
        dataloader_num_workers=0,
    )

    callbacks = []
    if eval_dataset:
        from transformers import EarlyStoppingCallback
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=3))

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        callbacks=callbacks,
    )

    logger.info("Starting training...")
    trainer.train(resume_from_checkpoint=True if os.path.exists(os.path.join(direction_output, "checkpoint-100")) else False)

    adapter_path = os.path.join(direction_output, "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    logger.info(f"Adapter saved to {adapter_path}")

    trainer_stats = trainer.state.log_history
    stats_path = os.path.join(direction_output, "training_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(trainer_stats, f, indent=2, ensure_ascii=False)
    logger.info(f"Training stats saved to {stats_path}")

    return adapter_path


def main():
    parser = argparse.ArgumentParser(description="Train LoRA adapter")
    parser.add_argument("--direction", default="report_writing",
                        choices=["react_protocol", "report_writing", "critic_review", "supervisor_dispatch"])
    parser.add_argument("--model-path", default="", help="Path to base model (defaults to LOCAL_MODEL_PATH)")
    parser.add_argument("--dataset-dir", default=str(Path(__file__).resolve().parent / "dataset"))
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "lora_output"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    args = parser.parse_args()

    settings = load_config()
    model_path = args.model_path or settings.local_model_path

    if not model_path:
        logger.error("No model path provided. Set LOCAL_MODEL_PATH in .env or use --model-path")
        sys.exit(1)

    train(
        direction=args.direction,
        model_path=model_path,
        dataset_dir=args.dataset_dir,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        gradient_accumulation=args.gradient_accumulation,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length,
    )


if __name__ == "__main__":
    main()
