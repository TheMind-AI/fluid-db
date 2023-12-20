from typing import List
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv
from themind.llm.func_instraction import instruct


# Load OI api key for the embeddings
load_dotenv()


class VectorStore(object):

    def __init__(self, local_storage_dir: str = "./"):
        self.vectorstore = Chroma(collection_name="all-data", persist_directory=local_storage_dir, embedding_function=OpenAIEmbeddings())
    
    def ingest(self, uid: str, data: List[str]):
        
        # for each chunk, crete a list a question which explain the chunk itself, similar how embeddings are being trained

        @instruct
        def find_question(text: str) -> List[str]:
            "Give me all the questions that give answears to this text: {text}"
            
        for chunk in data:
            question = find_question(chunk) 
            print(question)
        
        
        chunks = [
            Document(page_content=s, metadata={"uid": uid})
            for i, s in enumerate(data)
        ]
    
        self.vectorstore.add_documents(chunks)

    
    def query(self, uid: str, query: str):
        output = self.vectorstore.similarity_search(query="Alex is .. old", k=3, filters={"uid": uid})
        
        return output
    
    
    
if __name__ == '__main__':
    
    uid = 'test'
    
    # Process the CSV data
    csv_path = "/Users/zvada/Documents/TheMind/themind-memory/data/alex-rivera-ground-truth.csv"
    with open(csv_path, 'r') as file:
        sentences = file.read().splitlines()

    vec = VectorStore()
    
    vec.ingest(uid, sentences)
    
    output = vec.query(uid, "Alex is .. old")
    
    print(output)    


    