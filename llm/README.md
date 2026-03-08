# LLM模块 - 极简代码生成

## 功能

1. **嵌入**：获取文本嵌入向量（用于RAG）
2. **聊天**：与LLM对话
3. **代码生成**：生成单元测试并写入文件

## 配置

通过环境变量或`.env`文件配置：

```bash
CHAT_MODEL=openai/Qwen/Qwen2.5-7B-Instruct
API_KEY=sk-xxx
BASE_URL=https://api.siliconflow.cn/v1

EMBEDDING_MODEL=openai/BAAI/bge-m3
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
```

## 快速开始

### 1. 嵌入（用于RAG）

```python
from llm import embed, embed_single

# 批量嵌入
texts = ["代码1", "代码2", "代码3"]
embeddings = embed(texts)

# 单个嵌入
embedding = embed_single("这是一个方法")
```

### 2. 生成单元测试

```python
import asyncio
from llm import generate_test

async def main():
    result = await generate_test(
        class_name="Calculator",
        method_signature="public int add(int a, int b)",
        method_code="""
public int add(int a, int b) {
    if (a < 0 || b < 0) {
        throw new IllegalArgumentException("负数不支持");
    }
    return a + b;
}
""",
        output_path="./CalculatorTest.java",
        context=""  # 可选：从RAG检索到的上下文
    )
    
    if result["success"]:
        print(f"✓ 生成成功: {result['output_path']}")
    else:
        print(f"✗ 生成失败: {result['error']}")

asyncio.run(main())
```

### 3. 与RAG集成

```python
import asyncio
from llm import generate_test
from rag import CodeRAG

async def main():
    # 1. 检索上下文
    rag = CodeRAG("./gson.index")
    context = rag.get_context_for_prompt(target_method, top_k=5)
    
    # 2. 生成测试（带上下文）
    result = await generate_test(
        class_name="JsonReader",
        method_signature="public String nextString()",
        method_code=target_method,
        output_path="./JsonReaderTest.java",
        context=context  # RAG检索到的相关代码
    )

asyncio.run(main())
```

### 4. 批量生成

```python
import asyncio
from llm import batch_generate

tasks = [
    {
        "class_name": "Calculator",
        "method_signature": "public int add(int a, int b)",
        "method_code": "...",
        "output_path": "./AddTest.java"
    },
    {
        "class_name": "Calculator", 
        "method_signature": "public int subtract(int a, int b)",
        "method_code": "...",
        "output_path": "./SubtractTest.java"
    }
]

results = asyncio.run(batch_generate(tasks, max_concurrent=3))
```

## API

### 嵌入
```python
def embed(texts: List[str]) -> List[List[float]]
def embed_single(text: str) -> List[float]
```

### 聊天
```python
async def chat(prompt: str, system: str = None, **kwargs) -> str
```

### 代码生成
```python
async def generate_test(
    class_name: str,
    method_signature: str,
    method_code: str,
    output_path: str,
    context: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000
) -> Dict[str, any]

async def batch_generate(tasks: List[Dict], max_concurrent: int = 3) -> List[Dict]
```

### 提示词构建
```python
def build_test_prompt(
    class_name: str,
    method_signature: str,
    method_code: str,
    context: str = ""
) -> tuple[str, str]  # (system_prompt, user_prompt)
```

## 文件结构

```
llm/
├── __init__.py      # 模块导出
├── llm.py           # 核心实现（约200行）
└── README.md        # 本文档
```

就这么简单！
