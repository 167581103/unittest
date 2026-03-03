from rag.code_rag.code.code_rag import CodeRAG
from rag.code_rag.comment.comment_rag import CommentRAG
from langchain_litellm import ChatLiteLLM
from llm.llm import Embedding, Config

import os

qwen2_5 = ChatLiteLLM(
    model="litellm_proxy/Qwen/Qwen2.5-7B-Instruct",
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("BASE_URL")
)

bgem3 = Embedding(Config.setup())

voyage = Embedding(config={
    "embedding_model": "voyage/voyage-code-3",
    "embedding_api_key": os.getenv("VOYAGE_API_KEY"),
    "embedding_base_url": os.getenv("VOYAGE_BASE_URL")
})

java_file_path = '/home/juu/unittest/data/project/gson/gson/src/main/java/com/google/gson/stream/JsonReader.java'
index_path_comment = './comment_rag.index'
index_path_bgem3 = './bgem3_rag.index'
index_path_voyage = './voyage_rag.index'

comment_rag = CommentRAG(
    llm=qwen2_5,
    embeddings=bgem3,
    index_path=index_path_comment
)

bgem3_rag = CodeRAG(
    llm=qwen2_5,
    embeddings=bgem3,
    index_path=index_path_bgem3
)

voyage_rag = CodeRAG(
    llm=qwen2_5,
    embeddings=voyage,
    index_path=index_path_voyage
)

rag_list = [comment_rag, bgem3_rag]

# 测试对于同一个Query的不同RAG的结果
query = "下一个字符串"

for rag in rag_list:
    rag.load_documents(java_file_path)
    result = rag.generate_answer(query, rag.get_most_relevant_docs(query, top_k=3))
    print(result)