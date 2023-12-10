from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from themind.schema.location import Location
from themind.agents.chat_agent import ChatAgent
from fastapi.responses import StreamingResponse


router = APIRouter(prefix="/chat", tags=["chat"])


def sse_response(response, thread_id: str):
    sse = StreamingResponse(response,  media_type="text/event-stream")

    # workaround for app engine
    sse.headers["Cache-Control"] = "no-cache"
    sse.headers["X-Accel-Buffering"] = "no"
    sse.headers["Thread-ID"] = thread_id

    return sse


class MessageBody(BaseModel):
    uid: str
    thread_id: Optional[str] = None

    content: str

    location: Optional[Location] = None


@router.post("/chat")
async def chat_stream(body: MessageBody):

    # TOOD: improve this to use logger
    print('/chat')
    print(body)
    
    if body.thread_id is None:
        pass
        # create a new thread id

    from themind.firebase_app import firebase_app
    chat_agent = ChatAgent(firebase_app)
    response_stream, thread_id = chat_agent.chat(body.uid, body.thread_id, body.content, body.location)
    
    return sse_response(response_stream, thread_id=thread_id)
