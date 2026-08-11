# coding: utf-8
"""Local Qwen2.5 inference via transformers (GPU). Lazy singleton, asyncio-safe."""
import asyncio
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

import torch

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None
_gen_lock = threading.Lock()
_load_lock = threading.Lock()


def _load():
    global _model, _tokenizer
    with _load_lock:
        if _model is not None:
            return _model, _tokenizer
        from app.core.config import settings
        from transformers import AutoModelForCausalLM, AutoTokenizer

        path = settings.local_model_path
        logger.info(f"[local_qwen] Loading model from {path} ...")
        _tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            path,
            dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        _model.eval()
        logger.info("[local_qwen] Model loaded")
    return _model, _tokenizer


def _generate_blocking(messages: List[Dict[str, Any]], temperature: float, max_new_tokens: int) -> str:
    model, tokenizer = _load()
    with _gen_lock:
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=(temperature > 0),
                temperature=temperature,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[1]:]
        return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


async def generate(messages: List[Dict[str, Any]], temperature: float = 0.7, max_new_tokens: int = 4096) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_blocking, messages, temperature, max_new_tokens)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def generate_with_tools(system_prompt: str, user_prompt: str, tools: List[Dict],
                              max_rounds: int = 4, temperature: float = 0.7) -> Dict[str, Any]:
    """ReAct-style tool loop: model answers JSON {'tool': ..., 'args': ...} or {'answer': ...}."""
    tool_list = "\n".join(
        f"- {d['function']['name']}: {d['function'].get('description', '')} "
        f"args schema: {json.dumps(d['function'].get('parameters', {}), ensure_ascii=False)}"
        for d in tools
    )
    loop_instruction = (
        "Tools available:\n" + (tool_list or "(none)") + "\n\n"
        'Rules: When a tool is needed, reply with ONLY the JSON: {"tool": "<name>", "args": {...}}. '
        'When the final answer is ready, reply with ONLY the JSON: {"answer": "<text>"}. '
        "Never output anything other than that JSON."
    )
    messages = [
        {"role": "system", "content": f"{system_prompt}\n\n{loop_instruction}"},
        {"role": "user", "content": user_prompt},
    ]
    trace: List[Dict[str, Any]] = []

    for _ in range(max_rounds):
        resp = await generate(messages, temperature=temperature, max_new_tokens=1024)
        parsed = _parse_json(resp)
        if parsed and "answer" in parsed:
            return {"content": str(parsed["answer"]), "tool_calls": trace, "rounds": len(trace)}
        if parsed and "tool" in parsed and isinstance(parsed.get("args"), dict):
            name = str(parsed["tool"])
            args = parsed["args"]
            from app.tools.registry import call_tool
            try:
                result = await call_tool(name, **args)
            except Exception as e:
                result = {"status": "error", "message": str(e)[:200]}
            trace.append({"name": name, "args": args, "result": result})
            messages.append({"role": "assistant", "content": resp})
            messages.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False, default=str)[:4000]})
            continue
        messages.append({"role": "assistant", "content": resp})
        messages.append({"role": "user",
                         "content": 'Invalid response. Reply with exactly {"tool": "<name>", "args": {...}} or {"answer": "<text>"}.'})

    return {"content": "", "tool_calls": trace, "rounds": len(trace), "max_rounds_reached": True}