# -*- coding: utf-8 -*-
"""Fill due forward outcomes without changing frozen signal events."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import app
from company.model.ledger import update_outcomes


def main() -> int:
    as_of = sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()
    result = update_outcomes(as_of, app.fetch_history)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if os.environ.get("REQUIRE_DURABLE_LEDGER") == "1" and not result.get("durable"):
        print("Decision Ledger durable storage failed or is not configured.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
