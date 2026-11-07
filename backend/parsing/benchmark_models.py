"""Benchmark OpenRouter models on the real parser: accuracy, latency and cost per request.

Runs every case in fixtures/parser_cases.json + manual_cases.json through ``parse_request`` (the
same path the API uses) once per model, scoring exactly like run_manual_cases.py.

    .venv/bin/python -m backend.parsing.benchmark_models anthropic/claude-sonnet-5 anthropic/claude-haiku-5.5
    .venv/bin/python -m backend.parsing.benchmark_models --file models.txt --out bench.json

Needs OPENROUTER_API_KEY (billable). Prints a table sorted by median latency.
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from backend.inventory import load_inventory
from backend.parsing.adapter import parse_request
from backend.parsing.run_manual_cases import matches

ROOT = Path(__file__).resolve().parents[2]


def load_cases():
    fixture = json.loads((ROOT / "fixtures" / "parser_cases.json").read_text(encoding="utf-8"))["cases"]
    manual = json.loads(Path(__file__).with_name("manual_cases.json").read_text(encoding="utf-8"))["cases"]
    return [*fixture, *manual]


class TimingClient:
    """Wraps httpx: forces the model, records wall time, tokens and cost of each call."""

    def __init__(self, http, model):
        self.http, self.model = http, model
        self.calls = []
        self.lock = threading.Lock()

    def post(self, url, headers=None, json=None):
        json = {**json, "model": self.model, "usage": {"include": True}}
        start = time.perf_counter()
        response = self.http.post(url, headers=headers, json=json)
        elapsed = time.perf_counter() - start
        usage = {}
        try:
            usage = response.json().get("usage") or {}
        except Exception:
            pass
        with self.lock:
            self.calls.append({"seconds": elapsed, "status": response.status_code,
                               "cost": usage.get("cost") or 0.0,
                               "completion_tokens": usage.get("completion_tokens") or 0})
        return response


def run_case(http, model, case):
    client = TimingClient(http, model)
    inv = load_inventory()
    try:
        parsed = parse_request(inv, case["dish"], case["qty"], case["text"], client=client, mode="openrouter")
        ev = inv.evaluate_request(parsed.parser_output)
        ok = matches(case, parsed, ev)
        error = None
        got = {"parser": parsed.status, "inventory": ev.status, "ops": parsed.parser_output["ops"],
               "flags": parsed.parser_output["flags"]}
    except Exception as exc:
        ok, error, got = False, f"{type(exc).__name__}: {str(exc)[:160]}", None
    return {"id": case["id"], "ok": ok, "error": error, "got": got, "calls": client.calls}


def bench_model(model, cases, workers):
    with httpx.Client(timeout=90.0) as http, ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda c: run_case(http, model, c), cases))
    secs = [c["seconds"] for r in results for c in r["calls"] if c["status"] == 200]
    return {
        "model": model,
        "passed": sum(r["ok"] for r in results),
        "errors": sum(r["error"] is not None for r in results),
        "total": len(results),
        "median_s": statistics.median(secs) if secs else None,
        "p90_s": statistics.quantiles(secs, n=10)[-1] if len(secs) >= 2 else (secs[0] if secs else None),
        "cost_per_1k": 1000 * sum(c["cost"] for r in results for c in r["calls"]) / max(len(results), 1),
        "cases": results,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--file", help="text file with one model id per line")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests per model")
    ap.add_argument("--out", help="write full per-case results as JSON")
    args = ap.parse_args(argv)
    if not os.getenv("OPENROUTER_API_KEY"):
        sys.exit("Set the OPENROUTER_API_KEY environment variable")
    models = list(args.models)
    if args.file:
        models += [m.strip() for m in Path(args.file).read_text().splitlines() if m.strip() and not m.startswith("#")]
    cases = load_cases()

    rows = []
    for model in models:
        row = bench_model(model, cases, args.workers)
        rows.append(row)
        med = f"{row['median_s']:.2f}s" if row["median_s"] else "-"
        print(f"{model:45} {row['passed']:>3}/{row['total']}  errors {row['errors']:>2}  median {med}",
              file=sys.stderr, flush=True)

    fmt = lambda v: f"{v:.2f}" if v is not None else "-"
    print(f"\n{'model':45} {'acc':>7} {'err':>4} {'med s':>6} {'p90 s':>6} {'$/1k req':>9}")
    for r in sorted(rows, key=lambda r: (r["median_s"] is None, r["median_s"] or 0)):
        print(f"{r['model']:45} {r['passed']:>3}/{r['total']:<3} {r['errors']:>4} {fmt(r['median_s']):>6} "
              f"{fmt(r['p90_s']):>6} {r['cost_per_1k']:>9.2f}")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
