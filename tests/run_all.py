"""Прогон всех проверок: python tests/run_all.py"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ["test_core.py", "test_texts.py", "test_api.py", "test_handlers.py"]


def main() -> int:
    failed = []
    for name in TESTS:
        print(f"\n{'=' * 70}\n== {name}\n{'=' * 70}")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / name)],
            cwd=ROOT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            failed.append(name)

    print()
    if failed:
        print("ПРОВАЛЕНО:", ", ".join(failed))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
