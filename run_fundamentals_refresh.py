# -*- coding: utf-8 -*-
"""Refresh the durable 100-stock fundamentals snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from company.data.value_fundamentals import load_fundamentals, refresh_pool, save_fundamentals

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="refresh every stock in the current mother pool")
    parser.add_argument("--batch-size", type=int, default=5, help="oldest/incomplete stocks to refresh")
    args = parser.parse_args(argv)
    pool = json.loads((ROOT / "model_artifacts" / "active_pool.json").read_text(encoding="utf-8"))
    existing, load_meta = load_fundamentals()
    doc, run_meta = refresh_pool(pool, existing, batch_size=args.batch_size, force_all=args.full)
    saved = save_fundamentals(doc)
    print(json.dumps({"load": load_meta, "run": run_meta, "storage": saved}, ensure_ascii=False, indent=2))
    return 0 if saved.get("local_saved") and not run_meta["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
