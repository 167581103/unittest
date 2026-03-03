from comment_rag import CommentRAG

java_file_path = '/home/juu/unittest/data/project/gson/gson/src/main/java/com/google/gson/stream/JsonReader.java'
index_path = './comment_rag.index'

from langchain_litellm import ChatLiteLLM
from llm.llm import Embedding, Config

import os

llm = ChatLiteLLM(
    model="litellm_proxy/Qwen/Qwen2.5-7B-Instruct",
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("BASE_URL")
)

embeddings = Embedding(Config.setup())
rag = CommentRAG(llm=llm, embeddings=embeddings, index_path=index_path)
rag.load_documents(java_file_path)

query_list = ["下一个字符串", "检查是否有下一个元素"]
for query in query_list:
    relevant_docs = rag.get_most_relevant_docs(query, top_k=2)
    answer = rag.generate_answer(query, relevant_docs)

    print(f"【查询】: {query}")
    print("【相关文档】:")
    for i, doc in enumerate(relevant_docs, 1):
        print(f"  {i}. 方法签名: {doc['method_signature'][:80]}")
        print(f"     文档: {doc['comment'][:100]}...")
        print(f"     相似度: {doc.get('similarity_distance', 'N/A'):.4f}")
    print(f"【生成回答】: {answer}\n")