from typing import Optional
from themind.database.firestore.firestore_base import FirestoreBase
from themind.schema.user import User
from google.cloud.firestore_v1.base_query import FieldFilter


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

    def get_user_by_email(self, email: str) -> Optional[User]:
        collection_ref = self.collection_ref()
        query = collection_ref.where(filter=FieldFilter('email', '==', email)).stream()
        for result in query:
            return User(**result.to_dict())
        return None

    def create_user(self, user: User) -> User:
        existing_user = self.get_user_by_email(user.email)
        if existing_user is not None:
            raise Exception(f"A user with email {user.email} already exists.")

        collection_ref = self.collection_ref()
        user_dict = self.create(ref=collection_ref, data=user.model_dump())

        return User(**user_dict)
