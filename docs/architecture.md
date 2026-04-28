
# 架构与解耦设计

> 本项目的**核心贡献**是一套"单元测试生成方法论"，而**不是**一个运行时框架。
> 为此，仓库在架构上严格区分了：
>
> - **核心层**（core / llm / rag）—— 论文的方法论实现，零运行时假设；
> - **评估层**（evaluation / experiments）—— 使用 Maven/JaCoCo 做端到端验证的配套实验设施；
> - **演示层**（web） —— 面向 demo/答辩的交互外壳。
>
> 外部系统（CI、IDE 插件、其他语言的 Runner）只需复用**核心层**即可接入我们的方法论，
> 完全不必使用评估层。

## 1. 分层依赖图

```
┌─────────────────────────────────────────────────────────────┐
│              main.py  /  experiments/  /  web/              │ ← 薄壳层（entry points）
└────────────────────────────┬────────────────────────────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
┌────────────────────────┐       ┌─────────────────────────────┐
│  evaluation/           │       │        核心层（论文贡献）    │
│   └ evaluator.py       │       │                             │
│     · 跑 mvn test       │       │   ┌──────────────────────┐  │
│     · 解析 JaCoCo XML   │       │   │  llm/                │  │
│     · 计算覆盖率 Δ      │       │   │    llm.py            │  │
│                        │       │   │     analyze_method   │  │
│  experiments/          │       │   │     generate_test    │  │
│   ├ pick_methods.py    │       │   │     chat             │  │
│   ├ run_batch.py       │       │   │    prompts.yaml      │  │
│   └ rerender_report.py │       │   └──────────┬───────────┘  │
│     · 编排实验流水线    │       │              │               │
│     · 生成实验报告      │       │   ┌──────────▼───────────┐  │
└────────┬───────────────┘       │   │  core/               │  │
         │                       │   │    fix_loop.py       │  │
         └──── depends on ──────→│   │     rule_fix         │  │
                                 │   │     llm_fix          │  │
                                 │   │     fix_compile_errors│  │
                                 │   │    project_config.py │  │
                                 │   │     load_project     │  │
                                 │   └──────────┬───────────┘  │
                                 │              │               │
                                 │   ┌──────────▼───────────┐  │
                                 │   │  rag/                │  │
                                 │   │    CodeRAG           │  │
                                 │   │    AgenticRAG        │  │
                                 │   │    tree_parser.py    │  │
                                 │   └──────────────────────┘  │
                                 └─────────────────────────────┘
```

### 关键事实

实测依赖（2026-04-28 扫描）：

- `core/`、`llm/`、`rag/` 对 `evaluation/`、`experiments/` 的 `import` 数量 = **0**
- `evaluation/`、`experiments/` 对 `core/`、`llm/`、`rag/` 的 `import` 方向 = **单向向内**
- `web/server.py` 同时引用核心层和评估层，但它是独立的演示壳，可随时剥离

换句话说，**代码层面的解耦已经完成**，所缺的只是"对外说明"与"最小接入示例"。

## 2. 核心层对外接口

外部系统只需要下面 3 个入口函数：

```python
# ① 方法理解 + 测试用例设计（一次 LLM 调用）
from llm.llm import analyze_method
analysis = await analyze_method(
    class_name, method_signature, method_code,
    context=rag_context,       # 可选：RAG 检索到的相关代码
    junit_version=5,           # 4 或 5
)
# -> {"method_understanding": ..., "coverage_analysis": ..., "test_cases": [...]}

# ② 测试代码生成（skeleton + per-method 两步式）
from llm.llm import generate_test
gen = await generate_test(
    class_name, method_signature, method_code,
    output_path="/path/to/Generated.java",
    test_cases=analysis["test_cases"],
    junit_version=5,
)
# -> {"success": True, "methods_generated": 8, ...}

# ③ 编译失败自动修复（规则 + RAG + LLM 三层）
from core.fix_loop import fix_compile_errors
fixed_code, ok, fix_log = await fix_compile_errors(
    code=generated_code,
    compile_output=maven_or_javac_stderr,   # ← 外部系统提供的编译错误文本
    max_retries=3,
    compile_fn=lambda c: your_runtime.compile(c),  # ← 外部系统提供的 "重新编译一次" 回调
    junit_version=5,
)
```

**核心层对运行时的要求只有一条**：调用 `fix_compile_errors` 时传入一个 `compile_fn(code) -> (success, output)` 回调。
**我们不 care 这个回调背后是 Maven、Gradle、Bazel、javac、IDE 还是沙箱**。

## 3. 最小接入 Demo

见 `examples/external_integration.py`：
一个 <100 行的 Python 脚本，展示外部系统如何只 `import core / llm / rag`，
自己提供"编译回调"，就完成一次测试生成 + 修复闭环。**完全不 import `evaluation/` 或 `experiments/`**。

## 4. 评估层的定位

`evaluation/evaluator.py` 和 `experiments/run_batch.py` 是**我们自己用来做论文实验的脚手架**，
它负责：

- 跑 `mvn test` + JaCoCo，量化覆盖率提升
- 编排 Phase1 GEN → Phase2 PREFIX/EVAL/FIXLOOP → Phase3 REPORT 流水线
- 落盘 JSON / Markdown / CSV

**这部分不是论文的贡献，只是验证贡献的手段**，所以它被刻意地、明显地单独放在 `evaluation/` + `experiments/` 两个目录里。

## 5. 配置化原则

为了让核心层真正"与项目无关"，所有**项目特定**的东西都集中到 `data/projects.yaml`：

- JDK 路径、Maven module 名、`-Drat.skip=true` 等额外参数
- `junit_version: 4` 或 `5`
- JaCoCo / surefire argLine 兼容策略

增加一个新目标项目，通常只需要在 yaml 里加一节，不需要改任何 `.py` 文件。

## 6. 目录地图速查

```
core/          # 方法论核心：fix_loop、project_config
llm/           # LLM 接入 + prompts
rag/           # AgenticRAG 检索
evaluation/    # 评估设施（Maven/JaCoCo 包装）
experiments/   # 实验编排 + 报告生成
web/           # Demo 壳（可选）
data/          # projects.yaml 配置中心
docs/          # 本目录：规划、架构、实验记录
archive/       # 归档的历史脚本（只读）
```
