#!/usr/bin/env bash
# wait_and_rerun_zeroshot.sh
# ────────────────────────────────────────────────────────────────────────
# 看门狗：等主 batch（run_rerun_and_zeroshot.sh）跑完后，自动补跑 Z1。
#
# 背景：旧的 Z1 数据有 bug（evaluator jacoco_home 默认路径不存在，导致
# baseline / 测试运行全挂、覆盖率为空）。修复了 run_zeroshot_baseline.py
# 里的 TestEvaluator 初始化之后，需要重新跑 Z1_gson + Z1_cl 两场。
#
# 旧的 Z1 json/md 保留，不会被覆盖（新运行带新时间戳）。
# ────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")/.."
LOG=/tmp/paper_logs/_z1_rerun_watchdog.log
mkdir -p /tmp/paper_logs

{
  echo "[watchdog] started at $(date '+%F %T')"
  echo "[watchdog] waiting for main batch (bash experiments/run_rerun_and_zeroshot.sh) to finish..."
  # 等所有 run_rerun_and_zeroshot.sh 进程退出
  while pgrep -f "run_rerun_and_zeroshot.sh" > /dev/null; do
    sleep 30
  done
  echo "[watchdog] main batch finished at $(date '+%F %T'), sleeping 20s to let filesystem settle..."
  sleep 20

  echo "[watchdog] launching Z1 rerun (zeroshot only, 2 scenes)"
  bash experiments/run_rerun_and_zeroshot.sh zeroshot
  rc=$?
  echo "[watchdog] Z1 rerun finished at $(date '+%F %T'), exit=$rc"
} > "$LOG" 2>&1 &

echo "[watchdog] PID=$!  log=$LOG"
echo "[watchdog] 用 'tail -f $LOG' 查看进度"
