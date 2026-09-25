"""
FastAPI app: one JSON endpoint plus the static front-end.

    uvicorn app.main:app --reload
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import examples, personas, publishers
from .pipeline import run_plan

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Disco Campaign Planner", version="0.1")


class PlanRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    budget_usd: Optional[float] = Field(default=None, gt=0)
    deterministic_only: bool = False   # API/eval only: skip the LLM reranker and creatives, show fusion order


@app.post("/api/plan")
def plan(req: PlanRequest) -> dict:
    try:
        return run_plan(req.description, req.budget_usd, req.deterministic_only)
    except Exception as ex:  # noqa: BLE001 - surface the real cause to the UI
        raise HTTPException(status_code=502, detail=f"{type(ex).__name__}: {str(ex)[:400]}")


@app.get("/api/examples")
def get_examples() -> list[dict]:
    return examples()


@app.get("/api/catalog")
def get_catalog() -> dict:
    return {"publishers": [p.model_dump() for p in publishers()], "personas": [p.model_dump() for p in personas()]}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
