"""Merge a LoRA adapter into Gemma 4 E2B and register it with Ollama, e.g. `podcast-exec:r1`.

Steps: load the bf16 base on CPU -> merge the adapter -> save safetensors -> `ollama create` with the base
model's chat template and parameters, quantized to q4_K_M like the stock gemma4:e2b.

Usage (training venv):
  .venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/executor-r1 --tag podcast-exec:r1
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForImageTextToText, AutoProcessor

from evo.lora import BASE_MODEL


def merge(adapter: Path, out_dir: Path) -> None:
    model = AutoModelForImageTextToText.from_pretrained(BASE_MODEL, dtype=torch.bfloat16, device_map="cpu")
    model = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    model.save_pretrained(out_dir, safe_serialization=True)
    AutoProcessor.from_pretrained(BASE_MODEL).save_pretrained(out_dir)
    print(f"merged model saved to {out_dir}")


def modelfile_for(merged: Path, base_tag: str) -> str:
    """Reuse the stock model's TEMPLATE / PARAMETER / RENDERER lines so prompts are formatted identically."""
    base = subprocess.run(["ollama", "show", base_tag, "--modelfile"], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout
    base = re.sub(r'(?s)LICENSE\s+""".*?"""', "", base)
    base = re.sub(r"(?m)^#.*\n", "", base)
    return re.sub(r"(?m)^FROM .*$", f"FROM {merged.resolve().as_posix()}", base, count=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--tag", required=True, help="Ollama tag, e.g. podcast-exec:r1")
    ap.add_argument("--base-tag", default="gemma4:e2b")
    ap.add_argument("--quantize", default="q4_K_M")
    ap.add_argument("--merged", type=Path, default=None)
    args = ap.parse_args()

    merged = args.merged or Path("evo/merged") / args.tag.replace(":", "-")
    merge(args.adapter, merged)
    modelfile = merged / "Modelfile"
    modelfile.write_text(modelfile_for(merged, args.base_tag), encoding="utf-8")
    subprocess.run(["ollama", "create", args.tag, "-f", str(modelfile), "--quantize", args.quantize], check=True)
    print(f"created ollama model {args.tag}; use it with: podcast-agent run ... --model ollama_chat/{args.tag}")


if __name__ == "__main__":
    main()
