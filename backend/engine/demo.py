"""Run from the project root: python -m backend.engine.demo [--ai]."""
import argparse
import asyncio
from pathlib import Path

from . import EvaluationRequest, OpenRouterExplainer, evaluate_substitutions


async def main():
    parser = argparse.ArgumentParser(description="Synthetic restaurant forecasting demo")
    parser.add_argument("--ai", action="store_true", help="Enable live OpenRouter evidence selection")
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[2] /
                        "fixtures" / "evaluation_request.json")
    args = parser.parse_args()
    request = EvaluationRequest.model_validate_json(args.input.read_text())
    explainer = None
    if args.ai:
        try:
            explainer = OpenRouterExplainer.from_env()
        except ValueError as exc:
            parser.error(str(exc))
    result = await evaluate_substitutions(request, explainer)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
