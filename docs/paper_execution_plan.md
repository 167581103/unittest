#!/usr/bin/env markdown
# 论文实验执行 Plan（2026-04-28）

> 本文档是**执行版**，配合 `docs/paper_experiments.md`（设计版）一起用。
> 设计 = "要证什么 / 指标如何"；本 Plan = "具体怎么跑 / 怎么验收"。

---

## 1. 要证明的 5 条 Claim（再次对齐）

| # | Claim | 对应消融变量 |
|---|---|---|
| **C1** | 本方法论能在真实开源项目上稳定提升目标方法覆盖率 | 无消融，E1 主结果 |
| **C2** | 两步式生成（骨架 + per-method）比一步式不易截断、成功率更高 | `--one-shot` |
| **C3** | FixLoop（规则→LLM 三层）能把初次编译失败的测试拾回来 | `--fix-retries 0` |
| **C4** | 确定性 Prefix 修复在 FixLoop 之前以近零成本消掉一类错误 | `--no-prefix` |
| **C5** | AgenticRAG 对整个管线（生成/pre-import/修复）均有增量贡献 | `--no-rag`（全链路消融） |

---

## 2. 最终方法样本（已冻结）

| 项目 | 冻结文件 | N | 覆盖率分布 | 行数范围 |
|---|---|---|---|---|
| **gson** | `experiments/methods.yaml.gson.frozen_for_paper` | **9** | 71.4%–96.8%（全高档） | 14–31 |
| **commons-lang** | `experiments/methods.yaml.cl.frozen_for_paper` | **12** | 0.0%（全低档） | 23–67 |

**样本总数 21**。放弃"每项目强制三档均分"的目标，原因：
- gson 因本身测试充分，客观可选池（min_lines≥10 + 非重载 + cov<98%）只有 **9 个方法**
- cl 的 JaCoCo baseline exec 读出的所有候选方法均为 0% 覆盖（见 §5 已知问题）
- 两项目**联合**覆盖 0% / 71% / 93% / 96% 的完整覆盖率谱，具有论文说服力

**选取规则硬约束**（写在 pick_methods.py 里）：
- `--min-lines 10`：方法总行数 ≥10，否则 Δ 统计无意义
- 同名重载剔除：类内方法名必须唯一，避免 evaluator 的 `get_method_coverage(name)` 口径错位
- 排除 `<init> / hashCode / equals / toString`
- 排除 private 方法、排除"仅私有构造器的类"里的实例方法

---

## 3. 场次清单（10 场）

按顺序串行跑：

| # | 场次 ID | 项目 | Flag | 支撑 Claim | 预计 |
|---|---|---|---|---|---|
| 1 | `E1_gson` | gson | (默认全功能) | C1 | 30 min |
| 2 | `E1_cl` | cl | (默认全功能) | C1 | 40 min |
| 3 | `E2_gson` | gson | `--one-shot` | C2 | 20 min |
| 4 | `E2_cl` | cl | `--one-shot` | C2 | 30 min |
| 5 | `E3_gson` | gson | `--fix-retries 0` | C3 | 15 min |
| 6 | `E3_cl` | cl | `--fix-retries 0` | C3 | 25 min |
| 7 | `E4_gson` | gson | `--no-prefix` | C4 | 30 min |
| 8 | `E4_cl` | cl | `--no-prefix` | C4 | 40 min |
| 9 | `E5_gson` | gson | `--no-rag` | C5 | 30 min |
| 10 | `E5_cl` | cl | `--no-rag` | C5 | 40 min |

**合计预估 ≈ 5 小时**（比 12×N 估算少 30%，因为 gson 只有 9 个方法）。

---

## 4. 指标清单

### 4.1 主指标（写摘要/结论）
- `Δ line_cov`（目标方法自身，new−baseline，%-point）
- `Δ branch_cov`（目标方法自身）
- `compile_success_rate`
- `gen_success_rate`
- `tokens_per_covered_line`（总 tokens / 新增覆盖行数，成本效益核心指标）

### 4.2 过程指标（写正文主表）
- `initial_pass_rate`：无修复即通过
- `prefix_rescue_rate` / `fixloop_rescue_rate`
- `fix_attempts_avg`：FixLoop 平均轮数
- `rag_trigger_rate`：触发 AgenticRAG 再检索的轮次比例

### 4.3 成本指标（写成本小节）
- `tokens_by_phase`：analyze / skeleton / per_method / fix / rag
- `llm_calls_by_phase`
- `wall_time_total_s`

---

## 5. 已知问题（诚实记录）

### 5.1 cl 的 pick baseline exec 读出 0% 覆盖率
pick 端跑 `mvn test + jacocoagent` 时 cl 所有类的 exec 数据都是 0。
**但不影响 evaluator 端的 Δ 计算** —— evaluator 端每次独立跑 `-Dtest=<baseline>` 并读新 exec，已被 smoke 验证（`RandomStringUtils.random` baseline 91.7%，新 96.4%）。
**影响**：pick 阶段对 cl 的方法档位判定不可信 → 我们放弃 cl 的档位分层，承认 cl 样本全在低覆盖档。

### 5.2 gson 候选池只有 9 个
gson 本身测试覆盖率 91.8%，剔除小方法和同名重载后只剩 9 个。**不构造虚假样本，接受 N=9**。

---

## 6. 执行命令

### 6.1 跑全部 10 场
```bash
cd /data/workspace/unittest
nohup bash experiments/run_all_paper.sh > /tmp/paper_all.log 2>&1 &
```

### 6.2 单场补跑（故障时用）
```bash
bash experiments/run_all_paper.sh E3_gson     # 只跑 E3_gson
bash experiments/run_all_paper.sh E4          # 跑所有 E4_* 场次
```

### 6.3 产出命名
- JSON: `experiment_results/experiment_summary_twostep_<ts>_<scene>.json`
- MD: 同目录同前缀 `.md` 和 `.rerendered.md`
- 日志: `/tmp/paper_logs/<scene>.log`

---

## 7. 交付物清单（跑完后统一产出）

- [ ] 10 份 JSON（每场一份）
- [ ] 10 份 rerender 后的 MD（含方法级 Δ 明细）
- [ ] **场次汇总表**：10 行 × 主指标列，CSV + MD
- [ ] **方法级长表**：21 方法 × 5 场 = 105 行，CSV
- [ ] **FixLoop 过程表**：按 fix_log 解析
- [ ] **Token 分阶段表**：按 `tokens_by_phase`

前三项是论文主表，后两项是论文图。所有产出放在 `experiment_results/paper_final/`。

---

## 8. 故障恢复

- 单场失败：`bash experiments/run_all_paper.sh <scene_id>` 单独重跑
- LLM 代理限流：把 `--llm-concurrency` 从 4 降到 2（改 run_all_paper.sh）
- Maven 死锁：清理 `data/project/*/src/test/java/**/*Generated*.java` 再重跑
- 编排脚本本身挂了：每场有独立 `/tmp/paper_logs/<scene>.log`，按日志定位

---

## 9. 时间线（硬约束）

- **15:35** 启动 `run_all_paper.sh`
- **20:30 左右** 预计全部跑完
- **21:00** 汇总表 + 图生成
- **22:00** 论文数据可开始填写
