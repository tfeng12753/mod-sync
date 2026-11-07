"""Regenerate shared JSON Schemas: python -m backend.engine.export_contracts."""
import json
from pathlib import Path

from .schemas import (
    EvaluationRequest, EvaluationResult, ForecastRequest, ForecastResult, InventorySnapshot,
)
from .outlook import OutlookRequest, InventoryOutlook, ExtraAssessment
from .integration import ConnectedEvaluationRequest
from .decisions import ModificationRequest, ModificationDecision


def main():
    root = Path(__file__).resolve().parents[2] / "contracts"
    root.mkdir(exist_ok=True)
    for name, model in [
        ("evaluation_request", EvaluationRequest), ("evaluation_response", EvaluationResult),
        ("forecast_request", ForecastRequest), ("forecast_response", ForecastResult),
        ("inventory_snapshot", InventorySnapshot),
        ("outlook_request", OutlookRequest), ("outlook_response", InventoryOutlook),
        ("extra_assessment", ExtraAssessment),
        ("connected_evaluation_request", ConnectedEvaluationRequest),
        ("modification_request", ModificationRequest), ("modification_response", ModificationDecision),
    ]:
        schema = model.model_json_schema()
        if model is EvaluationRequest:
            # Direct evaluations require full recipes; connected evaluations
            # obtain them from inventory and may omit them in their candidates.
            candidate = schema["$defs"]["Candidate"]
            candidate["properties"]["requirements"].pop("default", None)
            candidate["properties"]["requirements"]["minItems"] = 1
            candidate["required"].append("requirements")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (root / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")


if __name__ == "__main__":
    main()
