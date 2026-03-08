# RAG模块 - 极简代码检索

## 功能

只做两件事：
1. **离线**：构建大型代码库的FAISS索引
2. **在线**：检索与目标方法相关的代码上下文

## 快速开始

### 1. 构建索引（离线，只需运行一次）

```python
from rag import CodeRAG

rag = CodeRAG()
rag.build_index(
    project_dir="/path/to/java/project",
    index_path="./code_rag.index"
)
```

### 2. 检索上下文（在线）

```python
from rag import CodeRAG

# 加载索引
rag = CodeRAG("./code_rag.index")

# 目标方法（要生成测试的方法）
target_method = """
public int add(int a, int b) {
    return a + b;
}
"""

# 检索相关上下文
context = rag.get_context_for_prompt(target_method, top_k=5)
print(context)
```

### 3. 与LLM模块集成

```python
from rag import CodeRAG
from llm import generate_unit_test

# 检索上下文
rag = CodeRAG("./code_rag.index")
rag_context = rag.get_context_for_prompt(target_method, top_k=5)

# 生成测试
result = await generate_unit_test(
    target_method=target_method,
    class_name="Calculator",
    method_name="add",
    return_type="int",
    parameters="int a, int b",
    output_path="./CalculatorTest.java",
    rag_context=rag_context,  # 将检索到的上下文传递给LLM
    test_context=""
)
```

## API

### CodeRAG类

```python
class CodeRAG:
    def __init__(self, index_path: Optional[str] = None)
    def build_index(self, project_dir: str, index_path: str, batch_size: int = 50)
    def search(self, query_method: str, top_k: int = 5) -> List[Dict]
    def get_context_for_prompt(self, query_method: str, top_k: int = 5) -> str
```

### 便捷函数

```python
def build_code_index(project_dir: str, index_path: str)
def retrieve_context(query_method: str, index_path: str, top_k: int = 5) -> str
```

## 文件结构

```
rag/
├── __init__.py      # 模块导出
├── code_rag.py      # 核心实现（约150行）
└── README.md        # 本文档
```

就这么简单！
