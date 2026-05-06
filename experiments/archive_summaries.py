#!/usr/bin/env python3
"""
experiments/archive_summaries.py
────────────────────────────────────────────────────────────────────────
将 experiment_results/ 根目录下生成的 experiment_summary_*.json / *.md /
*.rerendered.md 按 suffix 归档到 experiment_results/paper_final/summaries/。

★ 关键策略（幂等 + 零风险）：
  1. 始终是"复制"而非"移动"，原始文件保留，跑得再多次也不会丢
  2. 按 suffix（如 E1_gson / E1_gson_run2 / Z1_cl）分目录
  3. 同一场次如果有多次 run（时间戳不同），全部保留
  4. 正在写入的 summary 文件自动跳过（通过 mtime < now-30s 过滤）
  5. 运行示例：
       python experiments/archive_summaries.py           # 扫全部 + 归档
       python experiments/archive_summaries.py --dry-run # 预览不动手

输出目录结构：
  paper_final/summaries/
      E1_gson/
          20260429_144220.json
          20260429_144220.md
          20260429_144220.rerendered.md
          <新时间戳>.json     # 下一次重跑自动进来
      E1_cl/
      ...
      Z1_gson/
      _index.json                 # 自动生成的场次索引
"""

from __future__ import annotations
import argparse
import json
import re
import shutil
import time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "experiment_results"
DST = ROOT / "experiment_results" / "paper_final" / "summaries"

# 匹配文件名中的时间戳和 suffix
# 两种前缀：experiment_summary_twostep_ / experiment_summary_oneshot_ / experiment_summary_zeroshot_
PAT = re.compile(
    r"^experiment_summary_(?P<mode>twostep|oneshot|zeroshot)_"
    r"(?P<ts>\d{8}_\d{6})_"
    r"(?P<suffix>[A-Za-z0-9_]+?)"
    r"(?P<ext>\.rerendered\.md|\.json|\.md)$"
)


def parse_name(name: str):
    m = PAT.match(name)
    if not m:
        return None
    return m.groupdict()


def archive(dry_run: bool = False, min_age_sec: int = 30):
    """扫描 SRC 根目录（不递归），把符合命名规范的 summary 文件复制到 DST/<suffix>/"""
    now = time.time()
    stats = {"copied": 0, "skipped_existing": 0, "skipped_fresh": 0, "unrecognized": 0}
    index: dict[str, list] = {}

    for f in sorted(SRC.glob("experiment_summary_*")):
        if not f.is_file():
            continue
        info = parse_name(f.name)
        if info is None:
            stats["unrecognized"] += 1
            continue

        # 过滤掉刚写的文件（可能还在 flush）
        if now - f.stat().st_mtime < min_age_sec:
            stats["skipped_fresh"] += 1
            print(f"  ⏳ 跳过刚写入的文件: {f.name}")
            continue

        suffix = info["suffix"]
        ts = info["ts"]
        ext = info["ext"]
        target_dir = DST / suffix
        target_file = target_dir / f"{ts}{ext}"

        # 已归档 & 大小一致 -> 跳过
        if target_file.exists() and target_file.stat().st_size == f.stat().st_size:
            stats["skipped_existing"] += 1
        else:
            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target_file)
            stats["copied"] += 1
            print(f"  ✓ {f.name}  ->  summaries/{suffix}/{ts}{ext}")

        # 登记索引
        index.setdefault(suffix, []).append({
            "timestamp": ts,
            "mode": info["mode"],
            "ext": ext,
            "source": f.name,
            "size_bytes": f.stat().st_size,
        })

    # 写索引文件
    if not dry_run:
        DST.mkdir(parents=True, exist_ok=True)
        idx_path = DST / "_index.json"
        # 去重 & 稳定排序
        for k in index:
            seen = set()
            uniq = []
            for item in sorted(index[k], key=lambda x: (x["timestamp"], x["ext"])):
                key = (item["timestamp"], item["ext"])
                if key not in seen:
                    seen.add(key)
                    uniq.append(item)
            index[k] = uniq
        idx_path.write_text(
            json.dumps({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "scenes": index,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n📇 索引已写入: {idx_path.relative_to(ROOT)}")

    print("\n── 归档统计 ──")
    for k, v in stats.items():
        print(f"  {k:20s} = {v}")
    print(f"  场次数（suffix）     = {len(index)}")
    return stats, index


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览，不真复制")
    ap.add_argument("--min-age-sec", type=int, default=30,
                    help="跳过 mtime 新于这个秒数的文件（避免复制正在写入的）")
    args = ap.parse_args()
    archive(dry_run=args.dry_run, min_age_sec=args.min_age_sec)
