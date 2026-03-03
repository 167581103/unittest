# For bgem3 version
from rag.code_rag.code.code_rag import CodeRAG
from langchain_litellm import ChatLiteLLM
from llm.llm import Embedding, Config

import os

llm = ChatLiteLLM(
    model="litellm_proxy/Qwen/Qwen2.5-7B-Instruct",
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("BASE_URL")
)

embeddings = Embedding(Config.setup())

java_file_path = '/home/juu/unittest/data/project/gson/gson/src/main/java/com/google/gson/stream/JsonReader.java'
index_path = './code_rag.index'

rag = CodeRAG(llm=llm, embeddings=embeddings, index_path=index_path)
rag.load_documents(java_file_path)
results = rag.generate_answer("下一个字符串", rag.get_most_relevant_docs("下一个字符串", top_k=3))

print(results)
