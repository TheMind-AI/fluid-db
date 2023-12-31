from typing import Optional
from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.database.firestore.firestore_base import FirestoreBase


class ThreadRepository(FirestoreBase):

    def __init__(self, firebase_app):
        super().__init__(firebase_app)

    def collection_ref(self, **kwargs):
        uid = kwargs['uid']
        return self.db.collection('users').document(uid).collection('threads')
    
    def get_thread(self, uid: str, thread_id: str, include_posts=True) -> Optional[Thread]:
        collection_ref = self.collection_ref(uid=uid)
        
        thread_obj = self.get(ref=collection_ref, doc_id=thread_id)
        if thread_obj is None:
            return None
        
        thread = Thread(**thread_obj)

        if include_posts:
            posts_ref = collection_ref.document(thread_id).collection('posts')
            posts = [Message(**doc.to_dict()) for doc in posts_ref.stream()]
            thread.posts = posts
        
        return thread
    
    def update_thread(self, uid: str, thread: Thread):
        collection_ref = self.collection_ref(uid=uid)
        self.update(ref=collection_ref, doc_id=thread.id, data=thread.dict())
        
        posts_ref = collection_ref.document(thread.id).collection('posts')
        for post in thread.posts:
            self.update(ref=posts_ref, doc_id=post.id, data=post.dict())
