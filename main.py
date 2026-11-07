"""Run with: uvicorn main:app --reload"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from parser import ParseRequest, ParseResult, ParserValidationError, parse_modifications

logger = logging.getLogger(__name__)
app = FastAPI(title="ModSync Person 2 - Modification Parser", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "modsync-parser"}


@app.post("/parse", response_model=ParseResult)
def parse(request: ParseRequest):
    try:
        return parse_modifications(request)
    except ParserValidationError as exc:
        logger.warning("Model output rejected: %s", exc)
        raise HTTPException(status_code=502, detail="Parser result needs manual review; please retry") from exc
    except Exception as exc:
        logger.exception("Parsing request failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Unable to parse modifications") from exc
