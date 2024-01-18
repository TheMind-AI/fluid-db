from typing import Optional
from pathlib import Path
from abc import ABC, abstractmethod
from firebase_admin import firestore
import logging


class FirestoreBase(ABC):
    def __init__(self, firebase_app):
        self.firebase_app = firebase_app
        self.db = firestore.client(app=self.firebase_app)

    def get_project_root(self) -> str:
        return str(Path(__file__).parent.parent)

    @abstractmethod
    def collection_ref(self, **kwargs):
        pass

    def create(self, ref, data: dict) -> str:
        try:
            doc_ref = ref.add(data)
            if ref.id == 'users':
                data['uid'] = doc_ref[1].id
            else:
                data['id'] = doc_ref[1].id
            return data
        except Exception as e:
            logging.error(e)
            raise e

    def get(self, ref, doc_id: str) -> Optional[dict]:
        doc = ref.document(doc_id).get()
        if doc.exists:
            data = doc.to_dict()
            if ref.id == 'users':
                data['uid'] = doc_id
            else:
                data['id'] = doc_id
            return data
        else:
            logging.error(f"Document not found: {doc_id}")
            return None

    def update(self, ref, doc_id: str, data: dict) -> dict:
        doc_ref = ref.document(doc_id)
        doc_ref.update(data)
        if ref.id == 'users':
            data['uid'] = doc_id
        else:
            data['id'] = doc_id
        return data

    def delete(self, ref, doc_id: str) -> str:
        doc_ref = ref.document(doc_id)
        doc_ref.delete()
        return doc_id
