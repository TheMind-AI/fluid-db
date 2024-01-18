from themind.firebase_app import firebase_app
from firebase_admin import storage


class JsonStorage(object):
    
    def __init__(self):
        self.bucket = self._initialize_storage()

    def _initialize_storage(self):
        return storage.bucket(app=firebase_app)

    def get_json(self, uid, file_path):
        blob = self.bucket.blob(f'fluiddb/{uid}/{file_path}')
        return blob.download_as_text()

    def upsert_json(self, uid, file_path, data):
        blob = self.bucket.blob(f'fluiddb/{uid}/{file_path}')
        blob.upload_from_string(data)
