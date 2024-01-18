from typing import List
from pydantic import BaseModel
from langchain_core.documents import Document
from themind.llm.func_instraction import instruct
from themind.vectorstores.chunking.chunking_strategy import ChunkingStrategy


class QAModel(BaseModel):
    reasoning: List[str]
    question: List[str]
    answers: List[str]


class QuestionChunkingStrategy(ChunkingStrategy):

    def chunk(self, data: str) -> List[Document]:

        @instruct
        def find_question(text: str) -> QAModel:
            """
            Give me all the questions that can be answered based on the TEXT below.
            Use only the information you have in the TEXT. Don't add anything else.

            If you generate the question reply, use the same tone of voice as the TEXT.

            Don't ask any additional questions. Only the questions that can be answered based on the TEXT.

            Here is the TEXT:
            {text}
            """

        question = find_question(data)

        print('Chunk: ' + data)
        print(question)

        if len(question.question) == 0:
            return []

        if len(question.question) != len(question.answers):
            raise ValueError("The number of questions and answers do not match.")

        docs = []
        for q, a in zip(question.question, question.answers):
            content = f"{q}\n{a}"
            print('Content: ')
            print(content)
            doc = Document(page_content=content)
            docs.append(doc)

            # self.append_to_csv(q, a, chunk, filename='alex.csv')

        return docs
