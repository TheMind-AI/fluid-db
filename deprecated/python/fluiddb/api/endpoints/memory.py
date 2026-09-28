
from fastapi import APIRouter, HTTPException
from themind.memory.structured_json_memory import StructuredJsonMemory


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/memory/{uid}")
async def get_memory(uid: str):
    
    memory = StructuredJsonMemory()
    
    try:
        user_memory = memory.get_memory(uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    return user_memory

