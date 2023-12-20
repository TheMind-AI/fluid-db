from typing import List
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv
from themind.llm.func_instraction import instruct
from pydantic import BaseModel


# Load OI api key for the embeddings
load_dotenv()


class QAModel(BaseModel):
    reasoning: List[str]
    question: List[str]
    answers: List[str]


class VectorStore(object):

    def __init__(self, local_storage_dir: str = "./"):
        self.vectorstore = Chroma(collection_name="all-data", persist_directory=local_storage_dir, embedding_function=OpenAIEmbeddings())
    
    def ingest(self, uid: str, data: List[str], chunking_strategy: str = "questions"):

        # Question & Answear strategy
        # for each chunk, crete a list a question and answear from the text, similar how embeddings are being trained!

        @instruct
        def find_question(text: str) -> QAModel:
            """
            Give me all the questions that can be answered based on the TEXT below.
            Use only the information you have in the TEXT. Don't add anything else.
            
            If you generate the question reply, use the same tone of voice as the TEXT.

            Here is the TEXT: 
            {text}
            """

        chunks = []
        for chunk in data:
            question = find_question(chunk)

            print('Chunk: ' + chunk)
            print(question)

            if len(question.question) != len(question.answers):
                raise ValueError("The number of questions and answers do not match.")
            
            for q, a in zip(question.question, question.answers):
                content = f"{q}\n{a}"
                print('Content: ')
                print(content)
                doc = Document(page_content=content, metadata={"uid": uid})
                chunks.append(doc)

            print('Added chunk to vectorstore')
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


    