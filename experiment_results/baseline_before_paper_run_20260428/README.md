
# 2026-04-28 论文正式实验前的历史数据备份

本目录保存了在**正式开跑论文实验（添加 token 埋点、消融实验等之前）**
的所有历史实验产物，用于：

1. 防止后续重跑或代码修改意外覆盖/污染历史结果
2. 作为论文 "早期实验 vs 正式实验" 的对比素材
3. 保留 `fix_log` 等字符串形式的宝贵过程数据——里面记录了
   每次 FixLoop 的 `Attempt N: M errors (rule-fixable=X, hard=Y)` 等细节

## 主要内容

| 日期-suffix | 备注 |
|---|---|
| `20260426_204106` | 最早单方法 smoke |
| `20260426_213434` | 首次 top-12 实验（12 rows，fix_log 较全） |
| `20260426_220946` | 第二次调参 top-11 |
| `20260427_171621_verify_initial_uplift` | 验证 "首次通过率提升" 的专场（11 rows, 90.9% 编译成功率） |
| `20260427_190657_top12_continue` | 候选更新后的 top-12 续跑 |
| `20260427_203951_firstpass_fix_smoke` / `20260427_204205_firstpass_fix_smoke` | FixLoop 消融 smoke（3 方法） |
| `20260427_220714_top12_new` | **GSON 最完整的 top-12**（12/12 编译成功，avg line cov Δ=+20.19%） |
| `20260428_014859` | commons-lang 首次 top-12（JUnit 4/5 错配，0/12 编译成功，但 fix_log 非常详细） |
| `20260428_104302` / `20260428_104726_junit5_verify` | JUnit 5 适配后的首次 limit=1 验证 |
| `archive/reports_legacy/` | 更早期的单方法 exp_0x 报告 |

## 字段说明

这批数据**没有 token 埋点**（4/28 正式实验才加上）；
方法级覆盖率 Δ 需要通过 `experiments/rerender_report.py` 结合
`baseline_line_cov_from_pick + target_method_line_cov` 重新计算。

## 保留策略

**只读，不修改**。若需重新渲染，请拷贝到其他目录再处理。
