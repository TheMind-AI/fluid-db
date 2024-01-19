from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

class MongoEngine:

    def __init__(self):
        self.client = None
        self.db = None

    def query(self, uid: str, query: str):
        self.db_connection(uid)
        result = self.db.command(query)
        return result

    def schema(self, uid: str) -> str:
        db = self.db_connection(uid)
        collections = db.list_collection_names()
        schema_str = ""
        for collection_name in collections:
            collection = db[collection_name]
            schema_obj = collection.find_one()
            schema_str += f"Collection: {collection_name}\n"
            schema_str += self.get_schema(schema_obj, "")
            schema_str += "\n"
        return schema_str

    def get_schema(self, obj, indent):
        schema_str = ""
        for key in obj:
            if not callable(obj[key]):
                if isinstance(obj[key], dict):
                    schema_str += f"{indent}{key}: Object\n"
                    schema_str += self.get_schema(obj[key], indent + "\t")
                else:
                    schema_str += f"{indent}{key}: {type(obj[key]).__name__}\n"
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


if __name__ == '__main__':
    m = StructuredMongoMemory()
    m.dump("test")
