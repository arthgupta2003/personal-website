#!/usr/bin/env python3
"""Train the ML ranking model from attended event history.

Usage:
  uv run python scripts/train_model.py
  uv run python scripts/train_model.py --user 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recom.config import Settings
from recom.db import Database
from recom.ranking.ml_model import MLRanker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, default=1)
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.db_path)

    ranker = MLRanker()
    result = ranker.train(db)

    if "error" in result:
        print(f"Training failed: {result['error']}")
        sys.exit(1)

    print(f"Trained on {result['n_samples']} samples ({result['n_positive']} attended)")
    print("\nFeature importances (sorted by |coef|):")
    for name, coef in sorted(result["feature_importances"].items(), key=lambda x: -abs(x[1])):
        bar = "+" * int(abs(coef) * 20) if coef > 0 else "-" * int(abs(coef) * 20)
        print(f"  {name:<20} {coef:+.4f}  {bar}")


if __name__ == "__main__":
    main()
