
# 论文正式实验规划与数据口径（2026-04-28）

> 本文档定义论文所需的**全部实验场次**、**每场要收集的指标**、**每个指标的口径**。
> 已有历史数据（5 次 run、35+ 方法记录）备份在
> `experiment_results/baseline_before_paper_run_20260428/`，本次不再重复它们；
> 本轮开始后产出的 JSON 统一新鲜采集（含 token 埋点）。

---

## 1. 指标清单

### 1.1 主指标（必须有）

| 指标 | 定义 | 计算口径 | 来源字段 |
|---|---|---|---|
| **目标方法行覆盖率 Δ** | 被测方法自身行覆盖率的前后差值 | `target_method_line_cov − target_method_baseline_line_cov` | 新版 run_batch 直接写入 |
| **目标方法分支覆盖率 Δ** | 被测方法自身分支覆盖率的前后差值 | 同上 | 同上 |
| **编译成功率** | `compile_success=True` 的方法数 / 总方法数 | 最终状态（含 FixLoop 后） | `compile_success` |
| **生成成功率** | `gen_success=True` 的方法数 / 总方法数 | LLM 产出了语法有效的测试文件 | `gen_success` |
| **首次直接通过率** | `compile_success_stage == "initial"` 的比例 | 无任何修复即编译通过 | `compile_success_stage` |
| **Prefix 拾回率** | `prefix_rescued / prefix_attempted` | 确定性前置修复（规则 + import 补全）的单独贡献 | `_summary` 内 |
| **FixLoop 拾回率** | `fix_rescued / fix_attempted` | LLM + RAG 参与的迭代修复的单独贡献 | `_summary` 内 |

### 1.2 成本指标（token 埋点后新增）

| 指标 | 定义 |
|---|---|
| `tokens_prompt_total` | 一条方法全流程输入 token（analyze + skeleton + per-method + fix 全部累加） |
| `tokens_completion_total` | 一条方法全流程输出 token |
| `tokens_per_method_avg` | 平均每方法消耗 token |
| `tokens_per_covered_line` | **每新增一条覆盖行消耗的 token**（论文核心成本效益指标） |
| `tokens_by_phase` | 按阶段拆分：`analyze / generate_skeleton / generate_per_method / fix_loop / rag_retrieve` |
| `llm_calls_by_phase` | 每阶段 LLM 调用次数 |

### 1.3 FixLoop 过程指标（来自 fix_log 解析）

| 指标 | 定义 |
|---|---|
| `fix_attempts_used` | 实际用到几次 attempt（1 / 2 / 3） |
| `errors_before_fix` | 进入 FixLoop 时的编译错误数 |
| `errors_after_rules` | 每轮规则修复后剩余错误数 |
| `errors_after_llm` | 每轮 LLM 修复后剩余错误数 |
| `rule_vs_llm_contribution` | 规则 / LLM 各自消灭的错误数占比 |
| `rag_used_rounds` | 有多少轮触发了 RAG 再检索 |

### 1.4 效率指标

| 指标 | 来源 |
|---|---|
| `gen_duration_s` | Phase 1 单方法耗时（已有） |
| `eval_duration_s` | Phase 2 单方法耗时（已有） |
| `wall_time_total_s` | 整场实验总墙钟时间 |

---

## 2. 实验场次设计

### E1 — 核心完整实验（主结果）

| 场次 | 项目 | JUnit | 方法数 | 模式 | 说明 |
|---|---|---|---|---|---|
| **E1-gson** | gson | 4 | 12 | two_step + 全功能 | 论文主结果表第一行 |
| **E1-commons-lang** | commons-lang | 5 | 12 | two_step + 全功能 | 证明跨项目可扩展性 |

**目的**：证明"核心方法论在两个结构不同的真实开源项目上都成立"。

### E2 — 消融：是否启用两步式生成

| 场次 | 设置 | 对比点 |
|---|---|---|
| **E2a-gson-oneshot** | `--one-shot` | 单次 LLM 调用，生成整个测试类 |
| **E2b-gson-twostep** | （默认） | skeleton + 单方法拼装 |

（commons-lang 同样做一组，如果时间允许。）

**目的**：回答"两步式到底降没降截断率、提没提生成成功率"。

### E3 — 消融：是否启用 FixLoop

| 场次 | 设置 | 对比点 |
|---|---|---|
| **E3a-gson-no-fix** | `--fix-retries 0` | 禁用 FixLoop |
| **E3b-gson-full-fix** | `--fix-retries 3`（= E1-gson） | 启用 FixLoop |

**目的**：量化 FixLoop 的独立价值。`E3a.compile_success_rate − E3b.compile_success_rate` 即 FixLoop 的边际贡献。

### E4 — 消融：是否启用确定性 Prefix 修复

| 场次 | 设置 | 对比点 |
|---|---|---|
| **E4a-gson-no-prefix** | 关 prefix（需新增 flag） | 只依赖 LLM 生成 + FixLoop |
| **E4b-gson-with-prefix** | 开 prefix（= E1-gson） | 当前默认 |

**目的**：量化确定性规则修复层的独立贡献。

> ⚠️ `--no-prefix` flag 目前没有，需要在 E4 开跑前加一个。

### E5（可选）— 消融：是否启用 Agentic RAG

如果时间允许，用一个 flag 关闭 FixLoop 里的 `need_rag` 决策，强制 RAG 降级或关闭。对比主指标。

---

## 3. 实验执行顺序（今天的路线图）

1. ✅ **备份历史数据**（已完成）
2. ✅ **规划文档**（本文件）
3. ⏳ **等待正在跑的 commons-lang top-12**（`PID=1574737`，预计 45 分钟左右完成）
4. 🔨 **加 token 埋点到 `llm/llm.py` + `core/fix_loop.py` + `experiments/run_batch.py`**
5. 🔨 **加 `--no-prefix` / `--no-rag` 等消融 flag**
6. 🏁 **按 E1 → E2 → E3 → E4 顺序跑完所有场次**
7. 📊 每跑完一场立刻做快速摘要（不必全量统计），全部跑完后再统一做 P0 数据分析器

---

## 4. 命名与存档规范

每场实验 JSON 必须用清晰的 `--suffix`：

| 场次 | 命令 | 产出文件名样例 |
|---|---|---|
| E1-gson | `main.py run --project gson --suffix E1_gson_core` | `experiment_summary_twostep_YYYYMMDD_HHMMSS_E1_gson_core.json` |
| E1-commons-lang | `main.py run --project commons-lang --suffix E1_cl_core` | `…_E1_cl_core.json` |
| E2a | `main.py run --project gson --one-shot --suffix E2a_gson_oneshot` | `…_E2a_gson_oneshot.json` |
| E3a | `main.py run --project gson --fix-retries 0 --suffix E3a_gson_nofix` | `…_E3a_gson_nofix.json` |
| E4a | `main.py run --project gson --no-prefix --suffix E4a_gson_noprefix` | `…_E4a_gson_noprefix.json` |

所有实验跑完之后，用 `experiments/rerender_report.py` 统一把方法级 Δ 重新渲染。

---

## 5. 出表格式（给论文用）

为每场实验产出至少两层结果：

1. **方法级明细**（CSV）：`class, method, method_total_lines, baseline_line_cov, new_line_cov, line_cov_delta, baseline_branch_cov, new_branch_cov, branch_cov_delta, compile_stage, fix_attempts, tokens_in, tokens_out, gen_duration_s, eval_duration_s`
2. **实验级汇总**（CSV / MD）：每场实验一行，列包含 E1–E5 的对比
3. **FixLoop 解析**（CSV）：`attempt_id, method_id, errors_before, errors_after_rules, errors_after_llm, rag_triggered, tokens_in, tokens_out`

---

## 6. 风险与兜底

| 风险 | 兜底 |
|---|---|
| OpenAI/LLM 代理限流 | 降 `--llm-concurrency` 到 2；所有 chat 加重试 |
| 某场实验中途挂掉 | 每场用独立 `suffix`，写好 nohup 日志，断了就从 pick 输出恢复 |
| Token 字段在 SDK 不返回 | fallback 用 tiktoken 估算（次要但可接受） |
| commons-lang 额外问题 | 已通过 `projects.yaml.mvn_extra_args` 配置化兜底；若还有问题就加配置，不改第三方项目源码 |
