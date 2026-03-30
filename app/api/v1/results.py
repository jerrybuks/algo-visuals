import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.generation import Generation
from app.schemas.response import GenerateResponse

router = APIRouter()


@router.get("/results/{request_id}", response_model=GenerateResponse)
async def get_result(request_id: str, db: AsyncSession = Depends(get_db)) -> GenerateResponse:
    result = await db.execute(select(Generation).where(Generation.id == request_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"No result found for request_id: {request_id}")
    return GenerateResponse.model_validate_json(record.result_json)
