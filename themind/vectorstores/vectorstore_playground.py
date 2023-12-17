import uuid
import json
from langchain.embeddings import OpenAIEmbeddings
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.storage import InMemoryStore
from langchain.vectorstores import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv


load_dotenv()


# TODO: add experiment with the following:
# 1. for each chunk, crete a list a question which explain the chunk itself, similar how embeddings are being trained
# 2. ...

def save_to_vectorstore(uid: str, sentences):
    
    vectorstore = Chroma(collection_name="alex", embedding_function=OpenAIEmbeddings())

    chunks = [
        Document(page_content=s, metadata={"uid": uid})
        for i, s in enumerate(sentences)
    ]
    
    vectorstore.add_documents(chunks)

    return vectorstore

if __name__ == '__main__':

    uid = 'test'
    
    # Process the CSV data
    csv_path = "/Users/zvada/Documents/TheMind/themind-memory/data/alex-rivera-ground-truth.csv"
    with open(csv_path, 'r') as file:
        sentences = file.read().splitlines()

    vectorstore = save_to_vectorstore(uid, sentences)
    
    output = vectorstore.similarity_search(query="Alex is .. old", k=3, filters={"uid": uid})
    print(output)
    