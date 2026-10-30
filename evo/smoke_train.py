"""Check that LoRA training fits on this GPU: load 4-bit Gemma 4 E2B, run SFT and GRPO steps on a realistic
~2.5k-token example, and report time and peak memory.

  ollama stop gemma4:e2b && .venv-train/Scripts/python -m evo.smoke_train
"""

import time

import torch

from evo.lora import Example, encode, grpo, load_model, sft

t0 = time.time()
model, tok = load_model()
print(f"loaded in {time.time() - t0:.0f}s; GPU memory {torch.cuda.memory_allocated() / 2**30:.2f} GiB")
model.print_trainable_parameters()
long_prompt = "Transcript:\n" + "[T01 00:00] Host: We talk about podcasts and ads today. " * 180
examples = [Example("You are helpful.", long_prompt, '{\n  "reasoning": "short",\n  "summary": "' + "word " * 250 + '"\n}'),
            Example("You are helpful.", "Say hi as JSON.", '{\n  "text": "hi"\n}')]
print("tokens:", [encode(tok, e, 8192)[0].shape[1] for e in examples])
t0 = time.time()
sft(model, tok, examples, epochs=1, accum=1, log_every=1)
print(f"2 SFT steps in {time.time() - t0:.0f}s; peak GPU {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
examples[0].weight, examples[1].weight = 1.0, -1.0
t0 = time.time()
grpo(model, tok, examples, epochs=1)
print(f"GRPO pass in {time.time() - t0:.0f}s; peak GPU {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
