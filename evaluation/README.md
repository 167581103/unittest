# Evaluation模块 - 极简测试评估

## 功能

只做三件事：
1. **执行测试** - 编译并运行生成的单元测试
2. **收集结果** - 收集测试通过/失败情况
3. **评估覆盖率** - 使用JaCoCo评估代码覆盖率

## 快速开始

```python
from evaluation import TestEvaluator, print_report

# 初始化评估器
evaluator = TestEvaluator(
    project_dir="/home/juu/unittest/data/project/gson",
    jacoco_home="/home/juu/unittest/lib/jacoco-0.8.14"
)

# 评估测试
report = evaluator.evaluate(
    test_file="/tmp/JsonReaderTest.java",
    test_class="com.google.gson.stream.JsonReaderTest",
    target_class="com.google.gson.stream.JsonReader",
    target_method="skipValue"
)

# 打印报告
print_report(report)
```

## 评估流程

```
1. 复制测试文件 → 项目测试目录
2. 编译测试 → mvn test-compile
3. 运行测试 → mvn test（带JaCoCo代理）
4. 生成报告 → jacococli report
5. 解析结果 → 覆盖率数据
```

## 报告输出

```
============================================================
评估报告
============================================================

测试文件: /tmp/JsonReaderTest.java
目标类: com.google.gson.stream.JsonReader
目标方法: skipValue
编译成功: True

测试结果:
  ✓ testSkipValueWithArray
  ✓ testSkipValueWithObject

覆盖率报告:
  行覆盖率: 75.5% (45/60)
  分支覆盖率: 68.2%
  方法覆盖率: 100.0%

============================================================
```

## API

```python
class TestEvaluator:
    def __init__(self, project_dir: str, jacoco_home: str)
    def evaluate(self, test_file: str, test_class: str, 
                 target_class: str, target_method: str) -> EvaluationReport

class EvaluationReport:
    test_file: str
    target_class: str
    target_method: str
    test_results: List[TestResult]
    coverage: Optional[CoverageReport]
    compilation_success: bool
    errors: List[str]

class CoverageReport:
    class_name: str
    line_coverage: float
    branch_coverage: float
    method_coverage: float
    covered_lines: int
    total_lines: int
```

## 文件结构

```
evaluation/
├── __init__.py      # 模块导出
├── evaluator.py     # 核心实现（约250行）
├── run.sh          # 原有脚本（备用）
└── README.md        # 本文档
```

## 使用示例

### 命令行运行

```bash
conda activate gp
python evaluation/evaluator.py
```

### 与Pipeline集成

```python
import asyncio
from core.generate_test_pipeline import full_pipeline
from evaluation import TestEvaluator, print_report

# 1. 生成测试
result = await full_pipeline()

# 2. 评估测试
evaluator = TestEvaluator(
    project_dir="/home/juu/unittest/data/project/gson",
    jacoco_home="/home/juu/unittest/lib/jacoco-0.8.14"
)

report = evaluator.evaluate(
    test_file=result['output_path'],
    test_class="com.google.gson.stream.JsonReaderTest",
    target_class="com.google.gson.stream.JsonReader",
    target_method="skipValue"
)

# 3. 输出报告
print_report(report)
```

就这么简单！
