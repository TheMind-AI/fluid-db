# TODO: move function from agents to here
from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.database.firestore.thread_repository import ThreadRepository

class ThreadService:

    def __init__(self, firebase_app):
        self.thread_repo = ThreadRepository(firebase_app)

    def get_thread(self, uid: str, thread_id: str = None):
        if thread_id is None:
            thread = Thread.new_with_system_prompt(uid=uid)
        else:
            thread = self.thread_repo.get_thread(uid=uid, thread_id=thread_id)
            
        return thread
    
    def update_thread(self, uid: str, thread: Thread):
        self.thread_repo.update_thread(uid=uid, thread=thread)
