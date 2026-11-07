"""Primary demo: python -m backend.engine.outlook_demo --extra tofu 200."""
import argparse
import asyncio
import json
from pathlib import Path

from . import OpenRouterExplainer, OutlookRequest, assess_extra, forecast_inventory


async def main():
    parser = argparse.ArgumentParser(description="Synthetic demand, inventory, and extra-portion outlook")
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--extra", nargs=2, metavar=("INGREDIENT_ID", "QUANTITY"))
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[2] /
                        "fixtures" / "outlook_request.json")
    args = parser.parse_args()
    request = OutlookRequest.model_validate_json(args.input.read_text())
    try:
        explainer = OpenRouterExplainer.from_env() if args.ai else None
        outlook = await forecast_inventory(request, explainer)
        payload = {"outlook": outlook.model_dump(mode="json")}
        if args.extra:
            assessment = assess_extra(outlook, args.extra[0], float(args.extra[1]))
            payload["extra_assessment"] = assessment.model_dump(mode="json")
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
