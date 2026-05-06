#!/usr/bin/env bash
# experiments/run_zeroshot.sh
# ────────────────────────────────────────────────────────────────────────
# 论文对照实验：Zero-shot LLM Baseline（独立于 run_all_paper.sh）
#
# 场次：
#   Z1_gson : gson 项目 zero-shot
#   Z1_cl   : commons-lang 项目 zero-shot
#
# 用法：
#   bash experiments/run_zeroshot.sh            # 两个项目都跑
#   bash experiments/run_zeroshot.sh Z1_gson    # 只跑 gson
#   bash experiments/run_zeroshot.sh Z1_cl      # 只跑 commons-lang
#
# 与主实验的区别：
#   - 不调 RAG、不两步、不 prefix、不 FixLoop
#   - 使用独立脚本 experiments/run_zeroshot_baseline.py
#   - 方法集复用主实验的冻结 YAML，保证样本完全一致
# ────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PY:-/usr/bin/python}"
LOG_DIR="/tmp/paper_logs"
mkdir -p "$LOG_DIR"

GSON_YAML="experiments/methods.yaml.gson.frozen_for_paper"
CL_YAML="experiments/methods.yaml.cl.frozen_for_paper"

if [[ ! -f "$GSON_YAML" || ! -f "$CL_YAML" ]]; then
  echo "✗ 缺少冻结的 methods.yaml，先跑一遍 run_all_paper.sh 冻结再来。"
  exit 1
fi

run_one() {
  local scene="$1"
  local project="$2"
  local yaml_src="$3"

  echo ""
  echo "══════════════════════════════════════════════════════════════════════"
  echo "▶ ${scene}  project=${project}  (Zero-shot)"
  echo "══════════════════════════════════════════════════════════════════════"

  # 清理目标项目上一次 run 留下的 *Generated* 测试文件
  if [[ "$project" == "gson" ]]; then
    find data/project/gson/gson/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  else
    find data/project/commons-lang/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  fi

  local log_file="${LOG_DIR}/${scene}.log"
  local t0=$(date +%s)
  "$PY" experiments/run_zeroshot_baseline.py \
      --project "$project" \
      --methods-yaml "$yaml_src" \
      --suffix "$scene" \
      --llm-concurrency 4 \
      > "$log_file" 2>&1
  local rc=$?
  local t1=$(date +%s)
  local dt=$((t1 - t0))
  echo "[${scene}] exit=${rc}  elapsed=${dt}s  log=${log_file}"
}

declare -A SCENES
SCENES[Z1_gson]="gson $GSON_YAML"
SCENES[Z1_cl]="commons-lang $CL_YAML"

ORDER=(Z1_gson Z1_cl)

selected=()
if [[ $# -eq 0 ]]; then
  selected=("${ORDER[@]}")
else
  pat="$1"
  for s in "${ORDER[@]}"; do
    if [[ "$s" == ${pat}* || "$s" == *${pat}* ]]; then
      selected+=("$s")
    fi
  done
  if [[ ${#selected[@]} -eq 0 ]]; then
    echo "✗ 未匹配到任何场次：$pat"
    echo "可用: ${ORDER[*]}"
    exit 1
  fi
fi

echo "[zeroshot] 计划跑 ${#selected[@]} 场: ${selected[*]}"
TOTAL_T0=$(date +%s)

for scene in "${selected[@]}"; do
  # shellcheck disable=SC2086
  set -- ${SCENES[$scene]}
  project="$1"
  yaml_src="$2"
  run_one "$scene" "$project" "$yaml_src"
done

TOTAL_T1=$(date +%s)
TOTAL_DT=$((TOTAL_T1 - TOTAL_T0))

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "✅ Zero-shot 全部 ${#selected[@]} 场跑完；总耗时 ${TOTAL_DT}s ($((TOTAL_DT / 60)) min)"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "JSON 清单："
for s in "${selected[@]}"; do
  f=$(ls -t experiment_results/experiment_summary_zeroshot_*"${s}"*.json 2>/dev/null | head -1)
  [[ -n "$f" ]] && echo "  $s -> $f"
done
