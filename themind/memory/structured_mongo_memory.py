from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

class StructuredMongoMemory:

    def __init__(self):
        self.client = None
        self.db = None

    def query(self, uid: str, query: str):
        self.db_connection(uid)
        result = self.db.command(query)
        return result

    def schema(self, uid: str) -> str:
        self.db_connection(uid)
        collections = self.db.list_collection_names()
        schema_str = "\n".join(collections)
        return schema_str

    def db_connection(self, uid):
        connection_string = os.getenv('MONGO_CONNECTION_STRING')
        self.client = MongoClient(connection_string)
        self.db = self.client[uid]
        return self.db

    def dump(self, uid):
        self.db_connection(uid)
        collections = self.db.list_collection_names()

        for collection_name in collections:
            print(collection_name)
            collection = self.db[collection_name]
            result = collection.find()
            for doc in result:
                print(doc)
            print()