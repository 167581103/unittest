# TODO

## Fix Loop：编译失败后 LLM 自动迭代修复

**背景**：生成的测试代码编译失败时（如 `cannot find symbol`、API 不存在等），
目前流程直接报错退出，需要人工介入。

**方案**：
1. 编译失败后，解析 `javac` / Maven 的错误信息，提取出错的符号名和行号
2. 对每个 `cannot find symbol` 错误，通过 AgenticRAG 查询该符号的正确 API（类名、方法签名、所在包）
3. 将原始代码 + 编译错误 + 查询到的 API 信息一起喂给 LLM，让其修复
4. 重新编译，最多循环 N 次（建议 N=3）
5. 超过 N 次仍失败则放弃，记录到报告

**实现位置**：`evaluation/evaluator.py` 或新建 `core/fix_loop.py`

**关键接口**：
```python
async def fix_compile_errors(
    code: str,
    errors: list[str],
    agentic_rag: AgenticRAG,
    max_retries: int = 3,
) -> tuple[str, bool]:
    """返回 (修复后的代码, 是否成功)"""
```

---

## Template Injection：提供标准测试类骨架

**背景**：LLM 生成测试时，package / import / 类结构容易出错或遗漏。

**方案**：
在 Prompt 里直接给出标准骨架，让 LLM 只填充测试方法体：

```java
package {package};

import org.junit.Test;
import static org.junit.Assert.*;
// Add other imports as needed

public class {ClassName} {{
    // Fill in test methods here
}}
```

**实现位置**：`llm/prompts.yaml` 的 `test_user` prompt 中注入骨架模板

**注意**：骨架只固定结构，LLM 仍需自行补充被测类的 import，
需配合 `_fix_imports` 后处理兜底。
