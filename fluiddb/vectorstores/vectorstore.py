from typing import List
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv
from themind.llm.func_instraction import instruct
from pydantic import BaseModel
import csv
from themind.vectorstores.chunking.question_answer_strategy import QuestionChunkingStrategy
from themind.vectorstores.chunking.chunking_strategy import ChunkingStrategy


class VectorStore(object):

    def __init__(self, local_storage_dir: str = "./"):
        self.vectorstore = Chroma(collection_name="all-data", persist_directory=local_storage_dir, embedding_function=OpenAIEmbeddings())
    
    def ingest(self, uid: str, data: List[str], chunking_strategy: ChunkingStrategy = QuestionChunkingStrategy):

        # Question & Answear strategy
        # for each chunk, crete a list a question and answear from the text, similar how embeddings are being trained!

        for chunk in data:
            print('Chunk: ' + chunk)
            docs = chunking_strategy.chunk(uid, chunk)
            if len(docs) == 0:
                print('No documents were created for this chunk')
                continue

            # append metadata to its document
            for doc in docs:
                doc.metadata['uid'] = uid
                # doc.metadata['location'] = location
                # doc.metadata['created_at'] = created_at

            self.vectorstore.add_documents(docs)

        print('Added chunk to vectorstore')

    def query(self, uid: str, query: str):
        output = self.vectorstore.similarity_search(query=query, k=10, filters={"uid": uid})

        print(output)

        @instruct
        def answer(query: str, texts: List[str]) -> str:
            """
            This was a query user made: {query}
            This is a context we have: {texts}

            Reply:
            """

        return answer(query, [o.page_content for o in output])
    

if __name__ == '__main__':
    
    uid = 'test'
    
    # Process the CSV data
    csv_path = "/Users/zvada/Documents/TheMind/themind-memory/data/alex-rivera-ground-truth.csv"
    with open(csv_path, 'r') as file:
        sentences = file.read().splitlines()

    vec = VectorStore()
    
    vec.ingest(uid, sentences)
    
    # output = vec.query(uid, "what should i give laura for christmas?")
    output = vec.query(uid, "what is alex's favorite food?")
    
    print(output)    


    