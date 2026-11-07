"""Score the parser on fixtures/parser_cases.json + manual_cases.json.

    .venv/Scripts/python -m backend.parsing.run_manual_cases            # REAL model (needs OPENROUTER_API_KEY; billable)
    .venv/Scripts/python -m backend.parsing.run_manual_cases --offline  # rule-based fallback, no key

Review the ops for correctness, not merely the status.
"""

import argparse
import json
from pathlib import Path

from backend.inventory import load_inventory
from backend.parsing.adapter import parse_request

ROOT = Path(__file__).resolve().parents[2]


def _key(op):
    return (op["op"], op["ingredient"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use the rule-based parser (no API calls)")
    args = ap.parse_args(argv)
    mode = "offline" if args.offline else "openrouter"

    fixture = json.loads((ROOT / "fixtures" / "parser_cases.json").read_text(encoding="utf-8"))["cases"]
    manual = json.loads(Path(__file__).with_name("manual_cases.json").read_text(encoding="utf-8"))["cases"]
    passed = total = 0
    for case in [*fixture, *manual]:
        total += 1
        inv = load_inventory()
        try:
            parsed = parse_request(inv, case["dish"], case["qty"], case["text"], mode=mode)
        except Exception as exc:
            print(f"\nERROR {case['id']}: {case['text']!r}\n  {type(exc).__name__}: {str(exc)[:200]}")
            continue
        ev = inv.evaluate_request(parsed.parser_output)
        if "expected" in case:
            ok = (sorted(parsed.parser_output["ops"], key=_key) == sorted(case["expected"]["ops"], key=_key)
                  and ev.status == case["inventory_status"])
            expected = f"ops={case['expected']['ops']} inventory={case['inventory_status']}"
        else:
            ok = parsed.status == case["expected_status"]
            expected = f"parser={case['expected_status']} ({case['expected_summary']})"
        passed += ok
        print(f"\n{'PASS ' if ok else 'CHECK'} {case['id']}: {case['text']!r}")
        print(f"  expected: {expected}")
        print(f"  got:      parser={parsed.status} inventory={ev.status} ops={parsed.parser_output['ops']} "
              f"flags={parsed.parser_output['flags']}")
        for q in parsed.questions:
            print(f"  question: {q}")
    print(f"\n{passed}/{total} matched ({mode})")


if __name__ == "__main__":
    main()
