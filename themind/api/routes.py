from fastapi import APIRouter
from themind.api.endpoints.chat import router as chat_router
from themind.api.endpoints.user import router as user_router
from themind.api.endpoints.memory import router as memory_router

routers = APIRouter()
router_list = [chat_router, user_router, memory_router]

for router in router_list:
    routers.include_router(router)
