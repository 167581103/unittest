# UT-Gen：基于 LLM + RAG 的 Java 单元测试自动生成框架

> **目标**：给定任意 Maven 项目里的 `public` 方法，自动生成一份 JUnit 4 测试类，
> 并通过真实编译 + 运行 + JaCoCo 覆盖率做端到端评估。

## ✨ 最终实验成绩（主实验：`google/gson`）

- **12/12 个方法编译成功率 = 100%**
- **平均行覆盖率提升 +20.19%**，平均分支覆盖率提升 +10.87%
- **FixLoop 拾回率 6/6 = 100%**（首轮失败的全部通过 AI + RAG 辅助修复救活）
- 详见 [experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md](./experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md)

> **框架不锁死单一项目**：通过 [`data/projects.yaml`](./data/projects.yaml) 可以一键切换到任何 Maven 项目（仓库已预置 `gson` 和 `apache/commons-lang` 两个）：
>
> ```bash
> python main.py projects              # 查看所有可用项目
> python main.py pick --project commons-lang --top 12
> python main.py run  --project commons-lang
> ```

## 🧭 仓库导航

```
unittest/
├── main.py                    ← ★ 统一 CLI 入口（pick / run / report / web / clean / projects）
├── rag/                       代码 RAG 模块（Agentic 检索 + 向量索引）
├── llm/                       LLM 生成模块（骨架 + 逐方法，或一步式）
├── core/
│   ├── fix_loop.py            编译错误解析 + 规则修复 + LLM 修复循环
│   └── project_config.py      ← 多项目配置加载器（读 data/projects.yaml）
├── evaluation/
│   └── evaluator.py           Maven + JaCoCo 编译/测试/覆盖率评估器
├── experiments/               批量实验
│   ├── pick_methods.py        从工程中挨候选方法
│   ├── methods.yaml           当前候选方法集
│   └── run_batch.py           ★ 批量实验主脚本（被 main.py 调用）
├── web/                       FastAPI Demo Dashboard（可视化单方法流水线）
├── data/
│   ├── projects.yaml          ← 多项目配置清单（active / project_dir / module_name 等）
│   └── project/               第三方被测工程（只读）
│       ├── gson/
│       └── commons-lang/
├── experiment_results/        ★ 最终实验结果（其余旧实验在 archive/ 下）
├── archive/legacy/            早期探索阶段的废弃脚本（保留可追溯）
└── docs/                      实验笔记 / JaCoCo 原理说明```

## 🧱 四阶段流水线（每个被测方法都按这个顺序跑）

```
  STAGE 1  GEN       analyze_method → generate_test
                     LLM 先做测试用例设计，再生成 JUnit 代码
  STAGE 2  PREFIX    确定性前置修复（规则 + import 补全，不调 LLM）
                     先试图把"明显能修"的错就地消掉
  STAGE 3  EVAL      TestEvaluator.evaluate
                     Maven 编译 + 运行测试 + JaCoCo 取覆盖率
  STAGE 4  FIXLOOP   若 STAGE 3 仍编译失败 → Agentic RAG + LLM 修复循环
                     默认 3 轮，修通后自动回 STAGE 3 重新取覆盖率
```

最后 Phase 3 (`STAGE 5 REPORT`) 做失败归因 + 汇总 JSON / Markdown。

## 🚀 快速开始

```bash
# 0) 环境（一次性）
bash setup.sh

# 1) 选候选方法（从当前 active 项目中挨覆盖率最低的 N 个 public 方法）
python main.py pick --top 12
#   或一键切到另一个预置项目：
# python main.py pick --project commons-lang --top 12

# 2) 跑批量实验
python main.py run --llm-concurrency 4
# python main.py run --project commons-lang --llm-concurrency 4

# 3) 查看最新报告
python main.py report

# 4) 打开 Web 可视化 Dashboard（可选）
python main.py web

# 5) 列出所有可用的被测项目
python main.py projects

# 6) 清理 /tmp 下的生成产物（不会动实验结果）
python main.py clean
```
## ⚙️ 环境要求

| 组件 | 版本 | 路径 |
|---|---|---|
| Java | OpenJDK 17 | `/usr/lib/jvm/java-17-openjdk` |
| Maven | 3.9.6 | `/opt/maven-new` |
| JaCoCo | 0.8.14 | `lib/` |
| Python | 3.9+ | — |

Python 依赖：`pip install -r requirements.txt`

## 🔑 关键设计

- **两步式生成**：`analyze_method`（用例设计）→ `generate_test`（骨架 + 逐方法填充）
  比"一步式"更稳定、更易修复，默认启用（见 `--one-shot` 消融）。
- **确定性前置修复**：不花 LLM token 的第一道防线，覆盖常见 `cannot find symbol`
  / `package does not exist` / 私有成员访问 等场景。
- **Agentic RAG**：FixLoop 会基于编译错误决定是否触发检索，避免无脑检索浪费上下文。
- **骨架 + 逐方法生成**：骨架 prompt 固定 imports / 测试类壳，逐方法 prompt 只管 `@Test` 里的逻辑，
  显著降低长输出导致的截断。
- **端到端评估**：`baseline → 新测试 → Δ 覆盖率`，所有数字来自真实 Maven + JaCoCo 运行，
  不是 LLM 的自报数。

## 📂 验收交付

见 [DELIVERY.md](./DELIVERY.md)（一页纸）。

## 🗄️ 归档

- `archive/legacy/`：早期的 `batch_test_cross_class.py` / `verify_fixloop_single.py`
  / `smoke_test.py`，已被 `experiments/run_batch.py` 替代。保留是为了可追溯。
- `experiment_results/archive/`：开发过程中 10+ 份中间实验结果。
