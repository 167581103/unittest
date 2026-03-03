import faiss
import numpy as np

from llm.llm import client

offline_documents = [
    "RAG（检索增强生成）是一种结合信息检索和大语言模型的AI技术",
    "FAISS是Facebook开源的高效向量检索库，支持大规模向量相似度匹配",
    "文本嵌入的核心是将自然语言转换为计算机可理解的高维向量",
    "离线RAG的优势是无需联网，数据隐私性高，查询延迟低",
    "FAISS支持多种索引类型，其中IndexFlatL2是最基础的暴力检索索引"
]

# 离线阶段 - 构建文档嵌入和FAISS索引
def build_offline_rag_index(documents, save_path="./offline_rag_faiss.index"):
    # 1. 调用联网嵌入模型，生成文档的向量嵌入
    print("正在调用联网嵌入模型生成文档向量...")
    embed_response = client.embed(texts=documents)
    
    # 2. 解析嵌入响应，提取向量并转换为numpy数组
    doc_embeddings_list = []
    for data_item in embed_response["data"]:
        doc_embeddings_list.append(data_item["embedding"])
    doc_embeddings = np.array(doc_embeddings_list, dtype=np.float32)  # FAISS要求float32格式
    
    # 3. 获取向量维度
    vector_dim = doc_embeddings.shape[1]
    print(f"向量维度：{vector_dim}，文档数量：{len(documents)}")
    
    # 4. 构建FAISS索引
    faiss_index = faiss.IndexFlatL2(vector_dim)
    
    # 5. 向FAISS索引中添加文档向量
    faiss_index.add(doc_embeddings)
    
    # 6. 保存FAISS索引到本地（离线持久化，后续查询无需重新调用联网嵌入）
    faiss.write_index(faiss_index, save_path)
    print(f"FAISS索引已保存到：{save_path}")
    
    # 返回文档列表和索引保存路径
    return documents, save_path

# ---------------------- 步骤3：查询阶段 - 加载索引并实现向量检索（异步修改） ----------------------
def rag_query(user_question, documents, index_path, top_k=2):
    # 1. 加载本地保存的FAISS索引
    faiss_index = faiss.read_index(index_path)

    # 2. 调用联网嵌入模型，生成用户查询的向量嵌入
    query_embed_response = client.embed_single(text=user_question)
    query_embedding_list = [query_embed_response["data"][0]["embedding"]]
    query_embedding = np.array(query_embedding_list, dtype=np.float32)
    
    # 3. FAISS向量检索：返回top_k个最相似的结果
    distances, indices = faiss_index.search(query_embedding, top_k)
    
    # 4. 整理检索结果
    matched_docs = []
    for i, idx in enumerate(indices[0]):
        if idx < len(documents):  # 防止索引越界
            matched_docs.append({
                "similarity_distance": distances[0][i],
                "document_content": documents[idx]
            })
    
    # 5. 打印检索结果
    print(f"\n=== 用户查询：{user_question} ===")
    print(f"=== 检索到的{top_k}条相关文档 ===")
    for doc in matched_docs:
        print(f"相似度距离（越小越相似）：{doc['similarity_distance']:.4f}")
        print(f"文档内容：{doc['document_content']}\n")
    
    return matched_docs

# ---------------------- 执行完整异步流程 ----------------------
def main():
    # 第一步：离线构建索引（仅需执行一次，后续查询直接加载索引）
    docs, index_path = build_offline_rag_index(offline_documents)

    # 第二步：执行联网查询（可多次执行）
    rag_query("什么是FAISS？", docs, index_path)
    rag_query("离线RAG有什么好处？", docs, index_path)

if __name__ == "__main__":
    # 运行主程序
    main()