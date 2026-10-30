import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # data/, kb/, harness/ are resolved relative to the repo root


@pytest.fixture
def spec():
    from podcast_agent import harness

    return harness.load(ROOT / "harness/base.yaml")


@pytest.fixture
def fake_settings(tmp_path):
    from podcast_agent.config import Settings

    return Settings(fake_llm=True, use_cache=False, out_dir=tmp_path / "out")


@pytest.fixture
def noisy_txt(tmp_path):
    path = tmp_path / "ep900_test.txt"
    path.write_text(
        "# episode_id: ep900\n# title: Test Episode\n# host: Jamie Ortiz\n# guests: Dr. Lena Park\n"
        "# recorded: 2025-02-10\n"
        "[00:00:05] Host: Um, welcome back to the show, you know, it's great.\n"
        "[00:02:14] Host: So Nassa recently announced something about a new Mars mission.\n"
        "[00:02:18] Guest: Yes, it's launching in early 2026, they're planning to bring back samples.\n"
        "this line continues the previous turn.\n"
        "[00:03:01] Lena: And bit coin will hit $200K by 2025, uh, I'm sure.\n",
        encoding="utf-8",
    )
    return path
