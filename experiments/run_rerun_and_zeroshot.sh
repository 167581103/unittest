#!/usr/bin/env bash
# experiments/run_rerun_and_zeroshot.sh
# ────────────────────────────────────────────────────────────────────────
# 主实验补数脚本（2026-04-29）
#
# 目标：在 E1~E5 五场消融实验已跑完之后，补齐两类数据：
#   1. Zero-shot 基线：Z1_gson / Z1_cl （外部对照）
#   2. E1 主结果多轮重复：E1_gson_run2/run3、E1_cl_run2/run3
#      → 论文主表从"单轮数据"升级为"mean ± std"，消除 LLM 温度引起的点估计误差
#
# ★ 重要：所有重跑均使用带 _runN 的独立 suffix，因此：
#   - experiment_summary_*.json        文件名天然独立（带时间戳）
#   - experiment_results/paper_final/artifacts/<suffix>/ 独立子目录
#   - 旧的 E1_gson / E1_cl 原始数据完全不会被覆盖
#
# 用法：
#   bash experiments/run_rerun_and_zeroshot.sh                 # 全部跑
#   bash experiments/run_rerun_and_zeroshot.sh zeroshot        # 只跑 Z1
#   bash experiments/run_rerun_and_zeroshot.sh rerun           # 只跑 E1 重跑
#   bash experiments/run_rerun_and_zeroshot.sh E1_gson_run2    # 单场
# ────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PY:-/usr/bin/python}"
LOG_DIR="/tmp/paper_logs"
mkdir -p "$LOG_DIR"

ARTIFACT_ROOT="${ARTIFACT_ROOT:-${ROOT}/experiment_results/paper_final/artifacts}"
mkdir -p "$ARTIFACT_ROOT"

GSON_YAML="experiments/methods.yaml.gson.frozen_for_paper"
CL_YAML="experiments/methods.yaml.cl.frozen_for_paper"
LIVE_YAML="experiments/methods.yaml"

if [[ ! -f "$GSON_YAML" || ! -f "$CL_YAML" ]]; then
  echo "✗ 缺少冻结 yaml，请确认 run_all_paper.sh 已执行过。"
  exit 1
fi

# ─ 跑一场 Zero-shot ─
run_zeroshot_one() {
  local scene="$1" project="$2" yaml_src="$3"
  echo ""
  echo "══════════════════════════════════════════════════════════════════════"
  echo "▶ [Zero-shot] ${scene}  project=${project}"
  echo "══════════════════════════════════════════════════════════════════════"
  if [[ "$project" == "gson" ]]; then
    find data/project/gson/gson/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  else
    find data/project/commons-lang/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  fi
  local log="${LOG_DIR}/${scene}.log"
  local t0=$(date +%s)
  "$PY" experiments/run_zeroshot_baseline.py \
      --project "$project" \
      --methods-yaml "$yaml_src" \
      --suffix "$scene" \
      --llm-concurrency 4 \
      > "$log" 2>&1
  local rc=$?
  local dt=$(( $(date +%s) - t0 ))
  echo "[${scene}] exit=${rc}  elapsed=${dt}s  log=${log}"
}

# ─ 跑一场 E1 重跑（完全同 run_all_paper.sh 的 E1 参数，只换 suffix）─
run_e1_rerun_one() {
  local scene="$1" project="$2"
  echo ""
  echo "══════════════════════════════════════════════════════════════════════"
  echo "▶ [E1 rerun] ${scene}  project=${project}"
  echo "══════════════════════════════════════════════════════════════════════"
  local yaml_src
  if [[ "$project" == "gson" ]]; then
    yaml_src="$GSON_YAML"
    find data/project/gson/gson/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  else
    yaml_src="$CL_YAML"
    find data/project/commons-lang/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  fi
  cp "$yaml_src" "$LIVE_YAML"

  local log="${LOG_DIR}/${scene}.log"
  local t0=$(date +%s)
  "$PY" main.py run \
      --project "$project" \
      --suffix "$scene" \
      --llm-concurrency 4 \
      --artifact-root "$ARTIFACT_ROOT" \
      > "$log" 2>&1
  local rc=$?
  local dt=$(( $(date +%s) - t0 ))
  echo "[${scene}] exit=${rc}  elapsed=${dt}s  log=${log}"

  if [[ $rc -eq 0 ]]; then
    local json_file
    json_file=$(ls -t experiment_results/experiment_summary_*"${scene}"*.json 2>/dev/null | head -1)
    if [[ -n "$json_file" ]]; then
      "$PY" experiments/rerender_report.py "$json_file" >> "$log" 2>&1
      echo "[${scene}] rerendered: ${json_file%.json}.rerendered.md"
    fi
  fi
}

# ── 场次注册 ──────────────────────────────────────────────────────────
declare -A ZEROSHOT
ZEROSHOT[Z1_gson]="gson $GSON_YAML"
ZEROSHOT[Z1_cl]="commons-lang $CL_YAML"

declare -A RERUN
RERUN[E1_gson_run2]="gson"
RERUN[E1_cl_run2]="commons-lang"
RERUN[E1_gson_run3]="gson"
RERUN[E1_cl_run3]="commons-lang"

# 默认执行顺序：ZeroShot 先做（快，价值高），然后 E1 重跑 run2、run3
ORDER=(
  Z1_gson Z1_cl
  E1_gson_run2 E1_cl_run2
  E1_gson_run3 E1_cl_run3
)

# ── 解析过滤参数 ──────────────────────────────────────────────────────
selected=()
if [[ $# -eq 0 ]]; then
  selected=("${ORDER[@]}")
else
  pat="$1"
  case "$pat" in
    zeroshot|z1|Z1) selected=(Z1_gson Z1_cl) ;;
    rerun|E1rerun)  selected=(E1_gson_run2 E1_cl_run2 E1_gson_run3 E1_cl_run3) ;;
    run2)           selected=(E1_gson_run2 E1_cl_run2) ;;
    run3)           selected=(E1_gson_run3 E1_cl_run3) ;;
    *)
      for s in "${ORDER[@]}"; do
        if [[ "$s" == *${pat}* ]]; then
          selected+=("$s")
        fi
      done
      ;;
  esac
  if [[ ${#selected[@]} -eq 0 ]]; then
    echo "✗ 未匹配到场次：$pat"
    echo "可用: ${ORDER[*]}"
    echo "或别名: zeroshot / rerun / run2 / run3"
    exit 1
  fi
fi

echo "[rerun] 计划跑 ${#selected[@]} 场: ${selected[*]}"
TOTAL_T0=$(date +%s)

for scene in "${selected[@]}"; do
  if [[ -n "${ZEROSHOT[$scene]:-}" ]]; then
    # shellcheck disable=SC2086
    set -- ${ZEROSHOT[$scene]}
    run_zeroshot_one "$scene" "$1" "$2"
  elif [[ -n "${RERUN[$scene]:-}" ]]; then
    run_e1_rerun_one "$scene" "${RERUN[$scene]}"
  else
    echo "✗ 未知场次：$scene"
  fi
done

TOTAL_T1=$(date +%s)
TOTAL_DT=$((TOTAL_T1 - TOTAL_T0))

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "✅ 全部 ${#selected[@]} 场跑完；总耗时 ${TOTAL_DT}s ($((TOTAL_DT / 60)) min)"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "JSON 清单："
for s in "${selected[@]}"; do
  f=$(ls -t experiment_results/experiment_summary_*"${s}"*.json 2>/dev/null | head -1)
  [[ -n "$f" ]] && echo "  $s -> $f"
done
