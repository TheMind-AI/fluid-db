from typing import Optional
from themind.database.firestore_base import FirestoreBase
from themind.schema.user import User  # Assuming you have a User schema


class UserRepository(FirestoreBase):

    def __init__(self, firebase_app):
        super().__init__(firebase_app)

    def collection_ref(self, **kwargs):
        return self.db.collection('users')

    def get_user(self, uid: str) -> Optional[User]:
        collection_ref = self.collection_ref()
        user_obj = self.get(ref=collection_ref, doc_id=uid)
        if user_obj is None:
            return None
        return User(**user_obj)

    def create_user(self, user: User) -> str:
        collection_ref = self.collection_ref()
        return self.create(ref=collection_ref, data=user.model_dump())