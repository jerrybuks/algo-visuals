from fastapi import APIRouter

router = APIRouter()


@router.head("/health")
async def health() -> None:
    return None
