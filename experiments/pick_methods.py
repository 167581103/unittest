#!/usr/bin/env python3
"""
pick_methods.py —— 自动挑选"值得跑测试生成实验"的候选方法

流程：
  1. 用 gson 项目里**现成**的全量测试套件跑一次 JaCoCo（./mvnw test），得到全局 .exec
  2. 扫描 gson/src/main/java 下所有业务类
  3. 对每个类解析 JaCoCo XML，取方法级覆盖率
  4. 按挑选规则过滤 + 排序，输出 methods.yaml

挑选规则（和用户对齐）：
  - 行覆盖率 < 95%（默认阈值，可由 --max-coverage 调整）
  - 总行数 ≥ 8
  - 排除 private 方法（保留同包可访问方法）
  - 排除“仅 private 构造器类”的实例方法（避免 private access 编译失败）
  - 排除 <init> / <clinit> / toString / hashCode / equals
  - 每个类最多 2 个方法（保证跨类）
  - 按 "可挖掘价值" = 未覆盖行数 × 权重 排序，取 top-K

用法：
  python experiments/pick_methods.py --top 15 --min-lines 8 --max-coverage 70
"""
import os
import re
import sys
import json
import time
import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional

# 让脚本独立可跑，不依赖 PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.project_config import load_project, list_projects  # noqa: E402

# 项目相关的路径在 main() 里按 --project 加载；这里先放占位
PROJECT_DIR: str = ""
MODULE_DIR: str = ""       # 模块目录（单模块项目 = PROJECT_DIR）
SRC_ROOT: str = ""
JACOCO_HOME = "/data/workspace/unittest/lib"
BASELINE_EXEC: str = "/tmp/pick_methods_baseline.exec"
XML_REPORT: str = "/tmp/pick_methods_report.xml"
_CFG = None  # 全局 ProjectConfig，由 main() 或 CLI 初始化

# 跳过的方法名（低价值）
SKIP_METHODS = {"<init>", "<clinit>", "toString", "hashCode", "equals"}
# 跳过的类路径片段（测试辅助、内部工具）
SKIP_CLASS_PATHS = [
    "/annotations/",  # 注解类，方法通常只是字段
]


def _run_baseline(force: bool = False) -> bool:
    """跑一次全量测试，产出全局 .exec 文件。耗时 ~1~2 分钟。"""
    if not force and os.path.exists(BASELINE_EXEC) and os.path.getsize(BASELINE_EXEC) > 10_000:
        print(f"[baseline] 复用已有 exec: {BASELINE_EXEC} ({os.path.getsize(BASELINE_EXEC)} bytes)")
        return True

    print(f"[baseline] 跑全量测试（耗时 1~2 分钟）...")
    if os.path.exists(BASELINE_EXEC):
        os.remove(BASELINE_EXEC)

    # 根据项目配置选择合适的 JaCoCo 注入方式：
    #   - 有 argLine 的项目（commons-lang）：只能用 -DargLine，否则和 JAVA_TOOL_OPTIONS 双注入会崩溃
    #   - 没有 argLine 的项目（gson）：用 JAVA_TOOL_OPTIONS
    jacoco_agent = f"-javaagent:{JACOCO_HOME}/lib/jacocoagent.jar=destfile={BASELINE_EXEC}"
    tool_opts = _CFG.jacoco_tool_options(jacoco_agent)
    env_extra = {"JAVA_TOOL_OPTIONS": tool_opts} if tool_opts else {}
    env = _CFG.build_env(env_extra)

    cmd = ["mvn", "test"]
    cmd += _CFG.mvn_module_args()
    cmd += _CFG.jacoco_mvn_flags(jacoco_agent)
    cmd += [
        "-Dmaven.compiler.failOnWarning=false",
        "-Dmaven.test.failure.ignore=true",
        "-q",  # 安静模式，少打印
    ]
    cmd += list(_CFG.mvn_extra_args or [])
    t0 = time.time()
    res = subprocess.run(cmd, cwd=PROJECT_DIR, env=env,
                         capture_output=True, text=True, timeout=900)
    dt = time.time() - t0
    if not os.path.exists(BASELINE_EXEC) or os.path.getsize(BASELINE_EXEC) < 10_000:
        print(f"[baseline] ✗ 全量测试失败 (rc={res.returncode}, {dt:.1f}s)")
        print(res.stdout[-2000:])
        print(res.stderr[-2000:])
        return False
    print(f"[baseline] ✓ {BASELINE_EXEC} ({os.path.getsize(BASELINE_EXEC)} bytes, {dt:.1f}s)")
    return True


def _generate_xml_report() -> bool:
    """用 jacococli 把 exec 转成 xml。"""
    classfiles = os.path.join(MODULE_DIR, "target/classes")
    sourcefiles = SRC_ROOT
    if os.path.exists(XML_REPORT):
        os.remove(XML_REPORT)
    cmd = [
        "java", "-jar",
        f"{JACOCO_HOME}/lib/jacococli.jar",
        "report", BASELINE_EXEC,
        "--classfiles", classfiles,
        "--sourcefiles", sourcefiles,
        "--xml", XML_REPORT,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.exists(XML_REPORT):
        print(f"[xml] ✗ jacococli 失败\n{res.stderr}")
        return False
    print(f"[xml] ✓ {XML_REPORT} ({os.path.getsize(XML_REPORT)} bytes)")
    return True


def _read_source_for_class(full_class_name: str) -> Optional[str]:
    """根据全限定类名找 .java 源文件内容（只处理顶层类，内部类用顶层文件）。"""
    top = full_class_name.split("$")[0]  # 去掉内部类后缀
    rel = top.replace(".", "/") + ".java"
    path = os.path.join(SRC_ROOT, rel)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


# JVM descriptor 基础类型 → Java 源码类型（仅用于启发式匹配重载，粗糙够用）
_JVM_PRIMS = {
    "V": "void", "Z": "boolean", "B": "byte", "C": "char",
    "S": "short", "I": "int", "J": "long", "F": "float", "D": "double",
}


def _parse_desc_params(desc: str) -> List[str]:
    """解析 JVM descriptor 的参数类型列表（简化版，用于重载区分）。

    例如 (Ljava/lang/Class;)Z  →  ["java.lang.Class"]
         (Ljava/lang/reflect/Type;)Z  →  ["java.lang.reflect.Type"]
         (I[Ljava/lang/String;)V  →  ["int", "java.lang.String[]"]
    失败时返回 None。
    """
    if not desc or not desc.startswith("("):
        return None
    end = desc.find(")")
    if end < 0:
        return None
    params_raw = desc[1:end]
    params: List[str] = []
    i = 0
    while i < len(params_raw):
        arr_dim = 0
        while i < len(params_raw) and params_raw[i] == '[':
            arr_dim += 1
            i += 1
        if i >= len(params_raw):
            return None
        ch = params_raw[i]
        if ch in _JVM_PRIMS:
            ty = _JVM_PRIMS[ch]
            i += 1
        elif ch == 'L':
            semi = params_raw.find(';', i)
            if semi < 0:
                return None
            ty = params_raw[i + 1:semi].replace('/', '.')
            i = semi + 1
        else:
            return None
        params.append(ty + "[]" * arr_dim)
    return params


def _signature_matches_desc(sig_line: str, desc: str) -> bool:
    """判断 Java 源码签名 sig_line 是不是匹配 JVM descriptor desc。

    粗糙启发式：比对参数数量，以及每个参数类型的简单名是否都在签名里出现。
    """
    params = _parse_desc_params(desc)
    if params is None:
        return True  # 解析不出来就别过滤，避免误杀
    # 抽签名括号内的参数部分
    lparen = sig_line.find('(')
    rparen = sig_line.rfind(')')
    if lparen < 0 or rparen < 0 or rparen <= lparen:
        return False
    params_src = sig_line[lparen + 1:rparen].strip()
    if not params and not params_src:
        return True
    # 按顶层逗号切分（不考虑泛型里的逗号，粗糙够用）
    parts = []
    depth = 0
    cur = []
    for ch in params_src:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append(''.join(cur).strip())
    if len(parts) != len(params):
        return False
    for desc_type, src_part in zip(params, parts):
        # 取期望类型的简单名（去泛型 & 数组符号）
        simple = desc_type.split('.')[-1]
        simple_base = simple.replace('[]', '').strip()
        if not simple_base:
            continue
        # 简单名必须出现在源代码参数片段里
        if re.search(r'\b' + re.escape(simple_base) + r'\b', src_part) is None:
            return False
    return True


# 合法的方法头标志词：必须至少命中一个，否则不是真正的方法定义（可能是 Javadoc {@link}）
_METHOD_SIG_KEYWORDS = re.compile(
    r'\b(public|private|protected|static|final|abstract|synchronized|native|default)\b'
)


def _strip_comments_and_annotations(text: str) -> str:
    """去掉注释和注解，便于做可见性/static/构造器分析。"""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    text = re.sub(r'//.*', ' ', text)
    text = re.sub(r'@\w+(?:\([^)]*\))?', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _method_header_from_code(method_code: str) -> str:
    """从 method_code 中抽取方法头（到第一个 '{' 之前）。"""
    if not method_code:
        return ""
    brace = method_code.find('{')
    header = method_code[:brace] if brace >= 0 else method_code
    return _strip_comments_and_annotations(header)


def _extract_visibility_from_header(header: str) -> str:
    m = re.search(r'\b(public|protected|private)\b', header)
    return m.group(1) if m else "package"


def _is_static_header(header: str) -> bool:
    return bool(re.search(r'\bstatic\b', header))


def _class_only_has_private_constructors(source: str, simple_class: str) -> bool:
    """判断类是否显式声明了构造器且全部为 private。"""
    if not source or not simple_class:
        return False

    cleaned = re.sub(r'/\*.*?\*/', ' ', source, flags=re.DOTALL)
    cleaned = re.sub(r'//.*', ' ', cleaned)

    ctor_pattern = re.compile(
        rf'(?m)^\s*(public|protected|private)?\s*{re.escape(simple_class)}\s*\('
    )
    visibilities = []
    for m in ctor_pattern.finditer(cleaned):
        vis = (m.group(1) or "package").strip()
        visibilities.append(vis)

    if not visibilities:
        return False  # 没有显式构造器，默认构造器可用（按 Java 规则）
    return all(v == "private" for v in visibilities)


def _extract_method_snippet(source: str, method_name: str,
                            desc: str = "") -> Optional[Dict]:
    """从源文件里抽一个方法（尽量鲁棒）。

    策略：先找所有 `<method_name>(` 出现位置，向上回溯找该方法的签名起点
    （从当前位置往前，直到遇到 `{`/`}`/`;` 或文件开头，就认为那是上一个成员的边界），
    然后向前找到最近的 `{` 作为方法体起点，括号计数扫完整个方法体。

    若传入 desc（JVM descriptor），会按参数类型匹配指定的重载。

    返回 {"signature": "...", "code": "...", "start_line": N} 或 None
    """
    if not source:
        return None

    # 找 method_name(  的所有位置（不在字符串里）
    pattern = re.compile(r'\b' + re.escape(method_name) + r'\s*\(')
    for match in pattern.finditer(source):
        name_pos = match.start()

        # 向上回溯找签名起点：从 name_pos 往前，记录最后遇到的
        # `{` / `}` / `;` 的位置，下一个字符就是成员的起点
        boundary = -1
        for i in range(name_pos - 1, -1, -1):
            ch = source[i]
            if ch in '{};':
                boundary = i
                break
        sig_start = boundary + 1

        # 跳过开头的空白 / 注释 / annotation
        while sig_start < name_pos and source[sig_start] in ' \t\n\r':
            sig_start += 1

        # 方法体起点：从 name_pos 之后找第一个 `{`；若之前先遇到 `;`，说明这是
        # 一个方法声明（接口/抽象方法），或者不是方法定义
        body_start = -1
        stop = False
        i = match.end()
        while i < len(source):
            ch = source[i]
            if ch == ';':
                stop = True
                break
            if ch == '{':
                body_start = i
                break
            i += 1
        if stop or body_start == -1:
            continue

        # 括号计数扫完方法体
        depth = 0
        j = body_start
        body_end = -1
        while j < len(source):
            cj = source[j]
            if cj == '{':
                depth += 1
            elif cj == '}':
                depth -= 1
                if depth == 0:
                    body_end = j + 1
                    break
            j += 1
        if body_end == -1:
            continue

        # 拿到整段代码
        code = source[sig_start:body_end]
        if len(code) > 6000 or len(code) < 20:
            continue

        # 启发式：签名必须包含方法名，且前面不能是另一个 `new` 关键字（排除构造调用）
        sig_line = source[sig_start:body_start].strip().replace('\n', ' ')
        sig_line = re.sub(r'\s+', ' ', sig_line)
        if not sig_line or method_name not in sig_line:
            continue

        # 关键过滤：签名里必须含方法修饰符之一，否则不是真正的方法定义
        # （防止 Javadoc 里 `{@link #toJson(Object)}` 被误识别为方法）
        if not _METHOD_SIG_KEYWORDS.search(sig_line):
            continue

        # 排除 `@link` / `@see` 类型的 Javadoc 引用：签名前一小段里如果有 @link/@see/@code，丢弃
        preceding = source[max(0, sig_start - 30):sig_start]
        if re.search(r'@(?:link|linkplain|see|code|value)\b', preceding):
            continue

        # 排除 `new Foo() { ... }` 匿名类构造：前面紧跟 `new `
        if 'new ' in preceding and not any(
                kw in sig_line for kw in ('public', 'private', 'protected', 'static', 'void', 'return ')):
            # 不像方法签名（没有修饰符/返回类型），继续下一个匹配
            continue

        # 按 JVM descriptor 匹配重载：若给了 desc 但签名对不上，继续找下一个
        if desc and not _signature_matches_desc(sig_line, desc):
            continue

        start_line = source[:sig_start].count('\n') + 1
        return {"signature": sig_line, "code": code, "start_line": start_line}

    return None


def _pick_from_xml(
    top_k: int = 15,
    min_lines: int = 8,
    max_coverage: float = 70.0,
    max_per_class: int = 2,
) -> List[Dict]:
    """解析 XML 报告，按规则挑选候选方法。"""
    tree = ET.parse(XML_REPORT)
    root = tree.getroot()

    candidates: List[Dict] = []

    for pkg in root.findall(".//package"):
        pkg_name = pkg.get("name", "").replace("/", ".")
        for cls in pkg.findall("class"):
            cls_path = cls.get("name", "")  # com/google/gson/Foo
            if "$" in cls_path:
                continue  # 跳过内部类
            if any(skip in "/" + cls_path + "/" for skip in SKIP_CLASS_PATHS):
                continue
            full_cls = cls_path.replace("/", ".")
            simple_cls = cls_path.split("/")[-1]

            # 预读源文件一次
            source = _read_source_for_class(full_cls)

            # 收集该类的候选方法
            class_candidates: List[Dict] = []
            for method in cls.findall("method"):
                mname = method.get("name", "")
                if mname in SKIP_METHODS:
                    continue
                counters = {c.get("type"): c for c in method.findall("counter")}
                line_c = counters.get("LINE", {})
                branch_c = counters.get("BRANCH", {})
                line_missed = int(line_c.get("missed", 0)) if hasattr(line_c, "get") else 0
                line_covered = int(line_c.get("covered", 0)) if hasattr(line_c, "get") else 0
                total_lines = line_missed + line_covered
                if total_lines < min_lines:
                    continue
                line_cov = (line_covered / total_lines * 100) if total_lines > 0 else 0
                if line_cov >= max_coverage:
                    continue
                branch_missed = int(branch_c.get("missed", 0)) if hasattr(branch_c, "get") else 0
                branch_covered = int(branch_c.get("covered", 0)) if hasattr(branch_c, "get") else 0
                total_branches = branch_missed + branch_covered

                # 抽源码（没有就跳过，因为测试生成需要 method_code）
                if source is None:
                    continue
                mdesc = method.get("desc", "")
                snippet = _extract_method_snippet(source, mname, desc=mdesc)
                if snippet is None and mdesc:
                    # descriptor 精确匹配偶尔会因源码签名表达差异失配，做一次降级兜底
                    snippet = _extract_method_snippet(source, mname, desc="")
                if snippet is None:
                    continue

                # 新增：排除 private 方法（其余同包可访问方法保留）。
                header = _method_header_from_code(snippet["code"])
                visibility = _extract_visibility_from_header(header)
                is_static = _is_static_header(header)
                if visibility == "private":
                    continue

                # 新增：若类只有 private 构造器且目标方法是实例方法，通常很难稳定实例化，跳过。
                if (not is_static) and _class_only_has_private_constructors(source, simple_cls):
                    continue

                # "可挖掘价值" = 未覆盖行数 + 未覆盖分支 * 0.5
                value = line_missed + branch_missed * 0.5
                class_candidates.append({
                    "full_class_name": full_cls,
                    "simple_class_name": simple_cls,
                    "method_name": mname,
                    "method_desc": method.get("desc", ""),
                    "method_signature": snippet["signature"],
                    "method_code": snippet["code"],
                    "method_visibility": visibility,
                    "method_is_static": is_static,
                    "start_line": snippet["start_line"],
                    "total_lines": total_lines,
                    "covered_lines": line_covered,
                    "line_coverage": round(line_cov, 1),
                    "total_branches": total_branches,
                    "covered_branches": branch_covered,
                    "branch_coverage": round(
                        (branch_covered / total_branches * 100) if total_branches else 0, 1),
                    "value": round(value, 1),
                })

            # 每类最多 max_per_class 个（按 value 降序）
            class_candidates.sort(key=lambda x: -x["value"])
            candidates.extend(class_candidates[:max_per_class])

    # 全局按 value 降序排
    candidates.sort(key=lambda x: -x["value"])
    return candidates[:top_k]


def _write_yaml(candidates: List[Dict], out_path: str):
    """手写 YAML，避免再依赖 pyyaml 写端（读端已经有）。保证字段可控。"""
    lines = [
        "# 候选方法列表（由 experiments/pick_methods.py 自动生成）",
        "# 规则：行覆盖率 < --max-coverage（默认 95%）, 总行数 ≥ --min-lines（默认 5）, 排除 private 方法，跳过仅 private 构造器类的实例方法，每类最多 --max-per-class（默认 4）个，按未覆盖行价值排序",
        f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "methods:",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f"  - id: M{i:02d}")
        lines.append(f"    full_class_name: \"{c['full_class_name']}\"")
        lines.append(f"    simple_class_name: \"{c['simple_class_name']}\"")
        lines.append(f"    method_name: \"{c['method_name']}\"")
        # signature 里可能有引号，用单引号包
        sig = c['method_signature'].replace("'", "''")
        lines.append(f"    method_signature: '{sig}'")
        lines.append(f"    total_lines: {c['total_lines']}")
        lines.append(f"    covered_lines: {c['covered_lines']}")
        lines.append(f"    line_coverage: {c['line_coverage']}")
        lines.append(f"    branch_coverage: {c['branch_coverage']}")
        lines.append(f"    value: {c['value']}")
        # method_code 用 block scalar
        lines.append("    method_code: |")
        for code_line in c['method_code'].split('\n'):
            lines.append(f"      {code_line}")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[yaml] ✓ {out_path} ({len(candidates)} methods)")


def main():
    global _CFG, PROJECT_DIR, MODULE_DIR, SRC_ROOT, BASELINE_EXEC, XML_REPORT

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None,
                        help="目标项目名（来自 data/projects.yaml），省略则用 active 字段")
    parser.add_argument("--list-projects", action="store_true",
                        help="列出所有可用的项目并退出")
    # 默认值按 gson 这类高覆盖成熟项目调过：太严格会挑不满 top N。
    # 项目本身覆盖率普遍 ≥70%，所以放宽 max-coverage 到 95；
    # 小方法也要保留样本多样性，所以 min-lines=5；
    # 每类最多 4 个以覆盖重载，避免 12 个候选被 2~3 个类垄断。
    parser.add_argument("--top", type=int, default=15, help="输出候选数量")
    parser.add_argument("--min-lines", type=int, default=5)
    parser.add_argument("--max-coverage", type=float, default=95.0,
                        help="只保留行覆盖率 < 该值(%%) 的方法；默认 95")
    parser.add_argument("--max-per-class", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="强制重跑 baseline")
    parser.add_argument("--out", default=str(Path(__file__).parent / "methods.yaml"))
    args = parser.parse_args()

    if args.list_projects:
        for n in list_projects():
            print(n)
        return 0

    # 加载项目配置 → 写入全局
    _CFG = load_project(args.project)
    PROJECT_DIR = _CFG.project_dir
    MODULE_DIR = _CFG.module_dir
    SRC_ROOT = _CFG.src_main_java
    # 每个项目独立的 baseline/xml，避免串味
    BASELINE_EXEC = f"/tmp/pick_methods_baseline_{_CFG.name}.exec"
    XML_REPORT = f"/tmp/pick_methods_report_{_CFG.name}.xml"
    print(f"[project] 使用项目: {_CFG.name}  ({PROJECT_DIR})"
          + (f"  module={_CFG.module_name}" if _CFG.module_name else "  (单模块)"))

    if not _run_baseline(force=args.force):
        return 1
    if not _generate_xml_report():
        return 1
    candidates = _pick_from_xml(
        top_k=args.top,
        min_lines=args.min_lines,
        max_coverage=args.max_coverage,
        max_per_class=args.max_per_class,
    )
    if not candidates:
        print("[pick] ✗ 没有符合条件的候选方法")
        return 1

    print(f"\n[pick] 挑出 {len(candidates)} 个候选方法：")
    print(f"{'#':<3} {'类':<45} {'方法':<25} {'行':<8} {'覆盖率':<8} {'价值':<6}")
    for i, c in enumerate(candidates, 1):
        print(f"{i:<3} {c['simple_class_name']:<45} {c['method_name']:<25} "
              f"{c['covered_lines']}/{c['total_lines']:<4} "
              f"{c['line_coverage']:<7.1f}% {c['value']:<6}")

    _write_yaml(candidates, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
