from fastapi import APIRouter, HTTPException
from themind.database.firestore.user_repository import UserRepository
from themind.schema.user import User

router = APIRouter(prefix="/user", tags=["user"])

@router.post("/")
async def create_user(user: User):
    user_repo = UserRepository()
    try:
        user_id = user_repo.create_user(user)
        return {"uid": user_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{uid}")
async def get_user(uid: str):
    user_repo = UserRepository()
    try:
        user = user_repo.get_user(uid)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))