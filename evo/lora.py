"""LoRA training on Gemma 4 E2B in 8 GB of VRAM: shared by train_evolver.py and train_executor.py.

Memory plan for an 8 GB card (RTX 5060):
- linear layers in 4-bit NF4 (bitsandbytes) with bf16 compute, LoRA r=16 on attention + MLP projections
- the 2.35B-parameter per-layer embedding table (embed_tokens_per_layer) stays on the CPU: it is a lookup
- logits are computed only for completion tokens (logits_to_keep), since the 262k vocab makes full logits huge
- gradient checkpointing, batch size 1 with gradient accumulation
Run with the training venv (.venv-train) after unloading Ollama models (`ollama stop gemma4:e2b`).
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import AutoModelForImageTextToText, AutoTokenizer, BitsAndBytesConfig

BASE_MODEL = "unsloth/gemma-4-E2B-it"  # ungated mirror of google/gemma-4-E2B-it
LORA_TARGETS = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"


@dataclass
class Example:
    system: str
    prompt: str
    completion: str
    weight: float = 1.0  # SFT: 1; GRPO: the group-relative advantage
    group: str = ""


def load_model(adapter: str | Path | None = None, trainable: bool = True):
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    quant = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True, llm_int8_enable_fp32_cpu_offload=True,
        llm_int8_skip_modules=["vision_tower", "audio_tower", "embed_vision", "embed_audio", "lm_head"],
    )
    # Later entries override earlier ones: everything on GPU 0 except the per-layer embedding table.
    device_map = {"": 0, "model.language_model.embed_tokens_per_layer": "cpu"}
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL, quantization_config=quant, device_map=device_map, dtype=torch.bfloat16)
    _per_layer_embedding_on_cpu(model)
    model.config.use_cache = False
    if adapter:
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=trainable)
    elif trainable:
        model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                                                 target_modules=LORA_TARGETS, task_type="CAUSAL_LM"))
    if trainable:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    return model, tok


def _per_layer_embedding_on_cpu(model) -> None:
    """Make the per-layer embedding lookup actually run on the CPU.

    accelerate's offload hook would copy the whole 4.4 GiB table to the GPU on every forward pass, which on an
    8 GB card overflows into shared system memory and makes a training step take minutes instead of seconds.
    Instead: drop the hook, keep the table in CPU RAM (bf16), and move only the looked-up rows to the GPU.
    """
    from accelerate.hooks import remove_hook_from_module

    ple = model.model.language_model.embed_tokens_per_layer
    hook = getattr(ple, "_hf_hook", None)
    if hook is not None:
        hooks = getattr(hook, "hooks", [hook])  # a SequentialHook wraps several
        weight = next(h.weights_map["weight"] for h in hooks if getattr(h, "weights_map", None) is not None)
        remove_hook_from_module(ple)
        ple.weight = torch.nn.Parameter(weight.to("cpu", torch.bfloat16), requires_grad=False)
    ple.embed_scale = torch.tensor(ple.scalar_embed_scale)
    lookup = ple.forward
    ple.forward = lambda input_ids: lookup(input_ids.to("cpu")).to("cuda")


def encode(tok, ex: Example, max_len: int) -> tuple[torch.Tensor, int] | None:
    """Token ids of system+user+assistant, and the number of completion tokens at the end."""
    msgs = [{"role": "system", "content": ex.system}, {"role": "user", "content": ex.prompt}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(msgs + [{"role": "assistant", "content": ex.completion}], tokenize=False)
    if not full.startswith(prompt):  # template renders the assistant turn differently: fall back to plain concat
        full = prompt + ex.completion + (tok.eos_token or "")
    p_ids = tok(prompt, add_special_tokens=False).input_ids
    # The target ends at the end-of-turn token; the newline the template adds after it is never predicted.
    c_ids = tok(full[len(prompt):].rstrip("\n"), add_special_tokens=False).input_ids
    if len(p_ids) + len(c_ids) > max_len or not c_ids:
        return None
    return torch.tensor([p_ids + c_ids]), len(c_ids)


def completion_logprobs(model, ids: torch.Tensor, n_completion: int) -> torch.Tensor:
    """Per-token log-probs of the completion tokens (shape [n_completion])."""
    ids = ids.to("cuda")
    out = model(input_ids=ids, logits_to_keep=n_completion + 1)
    logits = out.logits[0, :-1].float()  # position i predicts token i+1
    targets = ids[0, -n_completion:]
    logprobs = torch.log_softmax(logits, dim=-1).gather(-1, targets[:, None]).squeeze(-1)
    # The first token is the opening "{" that Ollama's JSON grammar forces at inference; don't train on it.
    return logprobs[1:] if n_completion > 1 else logprobs


def sft(model, tok, examples: list[Example], epochs: int = 2, lr: float = 1e-4, accum: int = 4,
        max_len: int = 4096, log_every: int = 5) -> list[dict]:
    """Supervised fine-tuning on (prompt, completion) pairs; loss on completion tokens only."""
    encoded = [(e, enc) for e in examples if (enc := encode(tok, e, max_len))]
    print(f"SFT: {len(encoded)}/{len(examples)} examples fit in {max_len} tokens")
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
    history, step, t0 = [], 0, time.time()
    model.train()
    for epoch in range(epochs):
        random.Random(epoch).shuffle(encoded)
        for i, (ex, (ids, n)) in enumerate(encoded):
            loss = -(completion_logprobs(model, ids, n).mean()) * ex.weight
            (loss / accum).backward()
            if (i + 1) % accum == 0 or i == len(encoded) - 1:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
            history.append({"epoch": epoch, "i": i, "loss": round(loss.item(), 4)})
            if i % log_every == 0:
                print(f"  sft epoch {epoch} {i}/{len(encoded)} loss {loss.item():.4f} [{time.time() - t0:.0f}s]")
    return history


def grpo(model, tok, examples: list[Example], epochs: int = 1, lr: float = 5e-5, clip: float = 0.2,
         beta: float = 0.04, max_len: int = 4096) -> list[dict]:
    """Off-policy GRPO over logged groups: advantage-weighted, ratio-clipped policy gradient + KL to the base.

    Approximation (documented): samples came from the quantized Ollama model at temperature 0.9, not from this
    policy, so the "old" log-probs are this policy's log-probs before the update (ratio starts at 1).
    """
    encoded = [(e, enc) for e in examples if e.weight != 0 and (enc := encode(tok, e, max_len))]
    print(f"GRPO: {len(encoded)} examples with non-zero advantage")
    if not encoded:
        return []
    with torch.no_grad():
        old = [completion_logprobs(model, ids, n).detach() for _, (ids, n) in encoded]
        with model.disable_adapter():
            ref = [completion_logprobs(model, ids, n).detach() for _, (ids, n) in encoded]
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
    history = []
    model.train()
    for epoch in range(epochs):
        for i, (ex, (ids, n)) in enumerate(encoded):
            logp = completion_logprobs(model, ids, n)
            ratio = torch.exp(logp - old[i].to(logp.device))
            adv = torch.tensor(ex.weight, device=logp.device)
            pg = -torch.min(ratio * adv, torch.clamp(ratio, 1 - clip, 1 + clip) * adv)
            delta = ref[i].to(logp.device) - logp
            kl = torch.exp(delta) - delta - 1  # k3 estimator, as in the GRPO paper
            loss = (pg + beta * kl).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            opt.zero_grad(set_to_none=True)
            history.append({"epoch": epoch, "i": i, "loss": round(loss.item(), 4), "adv": ex.weight,
                            "kl": round(kl.mean().item(), 5)})
            print(f"  grpo {i}/{len(encoded)} adv {ex.weight:+.2f} loss {loss.item():+.4f} kl {kl.mean().item():.4f}")
    return history


def group_advantages(rows: list[dict]) -> list[float]:
    rewards = [r["reward"] for r in rows]
    mean = sum(rewards) / len(rewards)
    std = math.sqrt(sum((x - mean) ** 2 for x in rewards) / len(rewards))
    return [0.0 if std < 1e-6 else (x - mean) / (std + 1e-6) for x in rewards]


def save(model, out_dir: Path, history: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out_dir)
    (out_dir / "train_log.json").write_text(json.dumps(history, indent=1), encoding="utf-8")
    print(f"saved adapter to {out_dir}")
