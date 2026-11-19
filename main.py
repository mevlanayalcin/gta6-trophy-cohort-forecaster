#!/usr/bin/env python3
"""Entrypoint for make.fun's gta6-trophy-rarity-forecast-01 bounty.

Reads every *.in in MAKEFUN_INPUT_DIR (default /input), in name order,
and writes <id>.out to MAKEFUN_OUTPUT_DIR (default /output).  Python 3.13
standard library only; deterministic and run with -P -s.

The forecasting model lives in model.py beside this file.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import LEVELS, Z, forecast_case, logit_rate, inv_logit_rate  # noqa: E402


def _persistence_fallback(case: dict):
    """Valid output if the richer model ever fails on a malformed case."""
    trophies = case.get("trophies") or []
    history = case.get("history") or []
    horizon = case.get("horizon_minutes")
    if not isinstance(horizon, (int, float)) or not math.isfinite(horizon):
        horizon = 0.0
    sigma = 0.5 * math.sqrt(max(0.0, float(horizon)) / 1440.0)
    last = history[-1] if history else {}
    rates = last.get("rates") if isinstance(last, dict) else None
    if not isinstance(rates, list):
        rates = []
    rows = []
    for i, trophy in enumerate(trophies):
        tid = trophy.get("id", i + 1) if isinstance(trophy, dict) else i + 1
        r = rates[i] if i < len(rates) else None
        if not isinstance(r, (int, float)) or not math.isfinite(float(r)):
            r = 1.0
        r = min(99.99, max(0.01, float(r)))
        base = logit_rate(r)
        q = []
        prev = 0.0
        for z in Z:
            v = round(min(100.0, max(0.0, inv_logit_rate(base + z * sigma))), 4)
            if v < prev:
                v = prev
            q.append(v)
            prev = v
        rows.append({"id": tid, "q": q})
    return rows


def main() -> int:
    in_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR") or "/input")
    out_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 1

    try:
        files = sorted(p for p in in_dir.iterdir() if p.is_file() and p.name.endswith(".in"))
    except OSError:
        return 0

    for path in files:
        case_id = path.name[:-3]
        case = {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                case = json.load(fh)
            rows = forecast_case(case)
            if not isinstance(rows, list) or len(rows) != len(case.get("trophies") or []):
                raise ValueError("row count mismatch")
            for row in rows:
                if not isinstance(row.get("q"), list) or len(row["q"]) != len(LEVELS):
                    raise ValueError("bad row")
        except Exception:
            try:
                rows = _persistence_fallback(case if isinstance(case, dict) else {})
            except Exception:
                rows = []

        payload = json.dumps({"trophies": rows}, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > 65536:
            # The schema is tiny; this is unreachable, but stay valid.
            rows = _persistence_fallback(case if isinstance(case, dict) else {})
            payload = json.dumps({"trophies": rows}, separators=(",", ":"), allow_nan=False)
        try:
            (out_dir / (case_id + ".out")).write_text(payload, encoding="utf-8")
        except OSError:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
