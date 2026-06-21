from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROFILE = DATA / "profile"
PROCESSED = DATA / "processed"
RUNS = ROOT / "runs"
PROMPTS = ROOT / "prompts"
DB_PATH = PROCESSED / "experiment.sqlite"


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def ensure_dirs() -> None:
    for path in (RAW, PROFILE, PROCESSED, RUNS, PROMPTS):
        path.mkdir(parents=True, exist_ok=True)
