#!/usr/bin/env bash
# experiments/run_all_paper.sh
# ────────────────────────────────────────────────────────────────────────
# 论文 10 场正式实验串行编排脚本（2026-04-28）
#
# 场次：
#   E1: 全功能主结果        gson + cl
#   E2: 一步式消融          --one-shot
#   E3: FixLoop 消融        --fix-retries 0
#   E4: Prefix 消融         --no-prefix
#   E5: RAG 消融            --no-rag
#
# 每场结束后立即 rerender 出方法级版 MD，失败自动跳过但继续下一场。
#
# 使用：
#   bash experiments/run_all_paper.sh           # 跑全部 10 场
#   bash experiments/run_all_paper.sh E1        # 只跑 E1（gson + cl）
#   bash experiments/run_all_paper.sh E1_gson   # 只跑某一场
# ────────────────────────────────────────────────────────────────────────
set -u  # 注意：不 set -e，单场失败不应中断整个 pipeline

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PY:-/usr/bin/python}"
LOG_DIR="/tmp/paper_logs"
mkdir -p "$LOG_DIR"

# 实验产物根目录：每场一个子目录，保存 analyze/generation/prefix/fix 全轨迹
ARTIFACT_ROOT="${ARTIFACT_ROOT:-${ROOT}/experiment_results/paper_final/artifacts}"
mkdir -p "$ARTIFACT_ROOT"
echo "[paper] artifact root: $ARTIFACT_ROOT"

GSON_YAML="experiments/methods.yaml.gson.frozen_for_paper"
CL_YAML="experiments/methods.yaml.cl.frozen_for_paper"
LIVE_YAML="experiments/methods.yaml"

# 确保冻结 yaml 存在
if [[ ! -f "$GSON_YAML" || ! -f "$CL_YAML" ]]; then
  echo "✗ 缺少冻结的 methods.yaml，先 pick 一次（gson + cl）再冻结。"
  exit 1
fi

# 单场跑的函数
# run_one <场次ID> <项目> <额外flag>
run_one() {
  local scene="$1"   # E.g. "E1_gson"
  local project="$2" # gson / commons-lang
  shift 2
  local extra_flags=("$@")

  local yaml_src
  if [[ "$project" == "gson" ]]; then
    yaml_src="$GSON_YAML"
  else
    yaml_src="$CL_YAML"
  fi

  echo ""
  echo "══════════════════════════════════════════════════════════════════════"
  echo "▶ ${scene}  project=${project}  extra=${extra_flags[*]:-none}"
  echo "══════════════════════════════════════════════════════════════════════"

  # 1) 切换当前 yaml 到对应项目的冻结版
  cp "$yaml_src" "$LIVE_YAML"

  # 2) 清理目标项目上一次 run 留下的 *Generated* 测试文件，免得污染
  if [[ "$project" == "gson" ]]; then
    find data/project/gson/gson/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  else
    find data/project/commons-lang/src/test/java -name '*Generated*.java' -delete 2>/dev/null
  fi

  # 3) 跑实验（前台，按 suffix 落盘）
  local log_file="${LOG_DIR}/${scene}.log"
  local t0=$(date +%s)
  "$PY" main.py run \
    --project "$project" \
    --suffix "$scene" \
    --llm-concurrency 4 \
    --artifact-root "$ARTIFACT_ROOT" \
    "${extra_flags[@]}" \
    > "$log_file" 2>&1
  local rc=$?
  local t1=$(date +%s)
  local dt=$((t1 - t0))
  echo "[${scene}] exit=${rc}  elapsed=${dt}s  log=${log_file}"

  # 4) rerender 最新产出的 JSON（主指标改用方法级 Δ）
  if [[ $rc -eq 0 ]]; then
    local json_file
    json_file=$(ls -t experiment_results/experiment_summary_*"${scene}"*.json 2>/dev/null | head -1)
    if [[ -n "$json_file" ]]; then
      "$PY" experiments/rerender_report.py "$json_file" >> "$log_file" 2>&1
      echo "[${scene}] rerendered: ${json_file%.json}.rerendered.md"
    fi
  fi
}

# ── 注册所有场次 ─────────────────────────────────────────────────────────
declare -A SCENES
SCENES[E1_gson]="gson"
SCENES[E1_cl]="commons-lang"
SCENES[E2_gson]="gson --one-shot"
SCENES[E2_cl]="commons-lang --one-shot"
SCENES[E3_gson]="gson --fix-retries 0"
SCENES[E3_cl]="commons-lang --fix-retries 0"
SCENES[E4_gson]="gson --no-prefix"
SCENES[E4_cl]="commons-lang --no-prefix"
SCENES[E5_gson]="gson --no-rag"
SCENES[E5_cl]="commons-lang --no-rag"

# 默认执行顺序（主结果优先，消融次之，gson 在前 cl 在后）
ORDER=(
  E1_gson E1_cl
  E2_gson E2_cl
  E3_gson E3_cl
  E4_gson E4_cl
  E5_gson E5_cl
)

# ── 解析用户过滤参数 ─────────────────────────────────────────────────────
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

echo "[paper] 计划跑 ${#selected[@]} 场: ${selected[*]}"
TOTAL_T0=$(date +%s)

for scene in "${selected[@]}"; do
  cmd_line="${SCENES[$scene]}"
  # 拆成 project + 额外 flag 数组
  # shellcheck disable=SC2086
  set -- $cmd_line
  project="$1"
  shift
  run_one "$scene" "$project" "$@"
done

TOTAL_T1=$(date +%s)
TOTAL_DT=$((TOTAL_T1 - TOTAL_T0))

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "✅ 全部 ${#selected[@]} 场跑完；总耗时 ${TOTAL_DT}s ($(($TOTAL_DT / 60)) min)"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "JSON 清单："
for s in "${selected[@]}"; do
  f=$(ls -t experiment_results/experiment_summary_*"${s}"*.json 2>/dev/null | head -1)
  [[ -n "$f" ]] && echo "  $s -> $f"
done
