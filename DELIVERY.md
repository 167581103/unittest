# DELIVERY.md —— 验收一页纸

> **一句话**：给定一个 Java 工程里的任意 `public` 方法，
> 本框架自动生成 JUnit 4 测试、并通过真实 Maven 编译 + JaCoCo 覆盖率做端到端评估。

---

## 1. 最终成绩（主实验：`google/gson` 上 12 个低覆盖方法）

| 指标 | 数值 |
|---|---|
| 生成成功率 | **12/12 = 100%** |
| **编译成功率** | **12/12 = 100%** |
|  ├─ 首次直接成功 | 2 / 12 |
|  ├─ 确定性前置修复（规则 + import 补全）拾回 | 4 / 7 |
|  └─ FixLoop（LLM + Agentic RAG）拾回 | **6 / 6 = 100%** |
| **平均行覆盖率提升** | **+20.19%** |
| **平均分支覆盖率提升** | **+10.87%** |

详细表格见
[experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md](./experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md)。

> **框架可移植性**：由 `data/projects.yaml` 配置被测项目，仓库已预置 `gson`、`apache/commons-lang` 两个 Maven 项目。
> 通过 `python main.py projects` 可查看；通过 `--project <name>` 切换目标。

---

## 2. 归一化的四阶段流水线

```
┌────────── Phase 1（并行 LLM）──────────┐
│  STAGE 1  GEN      analyze → generate  │
└────────────────────────────────────────┘
┌────────── Phase 2（串行 Eval）─────────┐
│  STAGE 2  PREFIX   规则 + import 补全  │
│  STAGE 3  EVAL     Maven + JaCoCo      │
│  STAGE 4  FIXLOOP  LLM + RAG 修复循环  │
└────────────────────────────────────────┘
┌────────── Phase 3（汇总）──────────────┐
│  STAGE 5  REPORT   JSON + Markdown     │
└────────────────────────────────────────┘
```

运行时每个被测方法在日志中都会清晰看到：

```
  🧠 [GEN    ][m01] analyze: ReflectionAccessFilterHelper.isAnyPlatformType
  🧠 [GEN    ][m01] generate: 6 cases, one_shot=False
  🔧 [PREFIX ][m01] ✓ rounds=1, changes=1, compile_ok=True
  📊 [EVAL   ][m01] → baseline + compile + run + coverage
  🩹 [FIXLOOP][m01] → ...（仅当 EVAL 仍失败时）
  📝 [REPORT ]
```

---

## 3. 三行复现

```bash
bash setup.sh                                # 装 JaCoCo（一次性）
python main.py projects                      # 查看可用被测项目
python main.py pick --top 12                 # 选方法（~1min baseline，默认 active 项目）
python main.py run --llm-concurrency 4       # 跑实验，结果写到 experiment_results/
python main.py report                        # 看最新报告

# 如需切换项目：
# python main.py pick --project commons-lang --top 12
# python main.py run  --project commons-lang
```

---

## 4. 核心模块

| 模块 | 职责 | 关键文件 |
|---|---|---|
| `rag/` | 代码 RAG，离线建向量索引 + 在线 Agentic 检索 | `agentic_rag.py` / `code_rag.py` |
| `llm/` | 两步式生成（analyze + generate），prompt 管理 | `llm.py` / `prompts.yaml` |
| `core/` | **FixLoop**：编译错误解析 + 规则修复 + LLM 修复 | `fix_loop.py` |
| `evaluation/` | Maven + JaCoCo 编译/运行/覆盖率评估器 | `evaluator.py` |
| `experiments/` | 批量实验脚手架（选方法 + 跑 pipeline + 汇总） | `run_batch.py` / `pick_methods.py` |
| `web/` | FastAPI Demo Dashboard（可视化单方法流水线） | `server.py` / `static/index.html` |

---

## 5. 设计亮点

1. **确定性前置修复 (STAGE 2)**：不花 LLM token 的第一道防线，
   在真正进入昂贵的 FixLoop 之前就把"明显能修"的错修掉。
2. **FixLoop Agentic RAG**：基于编译错误种类**决策**是否触发检索，
   避免无脑检索浪费 LLM 上下文。
3. **骨架 + 逐方法生成**：骨架 prompt 固定 imports / 测试类壳，
   逐方法 prompt 只管 `@Test` 里的逻辑，显著降低长输出截断。
4. **端到端真实评估**：所有覆盖率数字来自真实 Maven + JaCoCo 运行，
   不是 LLM 的自报数。

---

## 6. 目录一览

```
unittest/
├── main.py                 ★ 统一 CLI 入口
├── README.md               总览
├── DELIVERY.md             本文件（验收一页纸）
├── rag/ llm/ core/ evaluation/ experiments/ web/   ← 6 个业务模块
├── experiment_results/     ★ 最终交付数据（+ archive/ 保存过程数据）
├── archive/legacy/         早期探索脚本（已归档，保留可追溯）
├── data/
│   ├── projects.yaml       ← 多项目配置清单（active / project_dir / module_name）
│   └── project/            被测工程（第三方，只读）
│       ├── gson/
│       └── commons-lang/
└── docs/ lib/ requirements.txt setup.sh
```
