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


def _mask_comments_and_strings(source: str) -> str:
    """把 Java 源码里的注释 / 字符串 / 字符字面量**就地替换为等长空白**。

    目的是在做"括号/分号回溯定位"时，防止 javadoc 里的 `{@link Foo#bar(X)}` 这类
    结构干扰——它们会被完全清除，但整个文件的字符 offset 保持不变，
    这样后续用 source[sig_start:body_end] 切片取到的内容与原始源码 offset 一致。

    被剥离的范围：
      - `/* ... */` 块注释（含 javadoc）
      - `// ...` 行注释
      - 双引号字符串字面量（含 `\\"` 转义）
      - 单引号字符字面量（含 `\\'`）
      - 文本块 \"\"\" ... \"\"\"（Java 15+）

    空白填充策略：换行保留、其余字符替换为空格。
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        c2 = source[i:i + 2]
        c3 = source[i:i + 3]
        # 文本块 """ ... """
        if c3 == '"""':
            out.append('   ')
            j = i + 3
            while j < n - 2 and source[j:j + 3] != '"""':
                out.append('\n' if source[j] == '\n' else ' ')
                j += 1
            if j < n - 2:
                out.append('   ')
                j += 3
            i = j
            continue
        # 块注释
        if c2 == '/*':
            out.append('  ')
            j = i + 2
            while j < n - 1 and source[j:j + 2] != '*/':
                out.append('\n' if source[j] == '\n' else ' ')
                j += 1
            if j < n - 1:
                out.append('  ')
                j += 2
            i = j
            continue
        # 行注释
        if c2 == '//':
            while i < n and source[i] != '\n':
                out.append(' ')
                i += 1
            continue
        # 字符串字面量
        if c == '"':
            out.append(' ')
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == '\\' and j + 1 < n:
                    out.append('  ')
                    j += 2
                    continue
                out.append('\n' if source[j] == '\n' else ' ')
                j += 1
            if j < n:
                out.append(' ')
                j += 1
            i = j
            continue
        # 字符字面量
        if c == "'":
            out.append(' ')
            j = i + 1
            while j < n and source[j] != "'":
                if source[j] == '\\' and j + 1 < n:
                    out.append('  ')
                    j += 2
                    continue
                out.append(' ')
                j += 1
            if j < n:
                out.append(' ')
                j += 1
            i = j
            continue
        out.append(c)
        i += 1
    masked = ''.join(out)
    # 安全保险：长度必须与原文一致，offset 才不会错位
    if len(masked) != n:
        # 出现罕见边界问题时退化为"整串保留"，避免把 offset 搞坏
        return source
    return masked


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
                            desc: str = "",
                            start_line_hint: Optional[int] = None) -> Optional[Dict]:
    """从源文件里抽一个方法（尽量鲁棒）。

    策略：先找所有 `<method_name>(` 出现位置，向上回溯找该方法的签名起点
    （从当前位置往前，直到遇到 `{`/`}`/`;` 或文件开头，就认为那是上一个成员的边界），
    然后向前找到最近的 `{` 作为方法体起点，括号计数扫完整个方法体。

    若传入 desc（JVM descriptor），会按参数类型匹配指定的重载。

    ★ 关键防护 1（javadoc）：先把"注释 + 字符串"就地替换为等长空白得到 `masked`，
      用 masked 做所有定位（查方法名、回溯成员边界、找 `{` 等），
      这样 Javadoc 里的 `{@link ClassName#method(...)}` 绝不会被当成真方法。
      最终切片仍从原始 `source` 里取，保留注释内容供 LLM 阅读。

    ★ 关键防护 2（重载）：若传入 `start_line_hint`（来自 JaCoCo XML 的 `line` 属性，
      指向方法体某行——通常是第一条可执行语句所在行），则优先选择其**起始行号最接近**
      hint 且 `start_line ≤ hint` 的那个重载。这比 desc 参数类型启发式更可靠。

    返回 {"signature": "...", "code": "...", "start_line": N} 或 None
    """
    if not source:
        return None

    masked = _mask_comments_and_strings(source)

    # 收集所有"看起来像方法定义"的候选
    pattern = re.compile(r'\b' + re.escape(method_name) + r'\s*\(')
    candidates: List[Dict] = []

    for match in pattern.finditer(masked):
        name_pos = match.start()

        # 向上回溯找签名起点：在 masked 上扫，这样 `{@link ...}` 的 `{` 已被消掉
        boundary = -1
        for i in range(name_pos - 1, -1, -1):
            ch = masked[i]
            if ch in '{};':
                boundary = i
                break
        sig_start = boundary + 1

        # 跳过开头的空白
        while sig_start < name_pos and masked[sig_start] in ' \t\n\r':
            sig_start += 1

        # 方法体起点：从 name_pos 之后找第一个 `{`
        body_start = -1
        stop = False
        i = match.end()
        while i < len(masked):
            ch = masked[i]
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
        while j < len(masked):
            cj = masked[j]
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

        code = source[sig_start:body_end]
        if len(code) > 6000 or len(code) < 20:
            continue

        sig_line_masked = masked[sig_start:body_start].strip().replace('\n', ' ')
        sig_line_masked = re.sub(r'\s+', ' ', sig_line_masked)
        if not sig_line_masked or method_name not in sig_line_masked:
            continue

        if not _METHOD_SIG_KEYWORDS.search(sig_line_masked):
            continue

        preceding = masked[max(0, sig_start - 30):sig_start]
        if re.search(r'@(?:link|linkplain|see|code|value)\b', preceding):
            continue

        if 'new ' in preceding and not any(
                kw in sig_line_masked for kw in ('public', 'private', 'protected', 'static', 'void', 'return ')):
            continue

        sig_line_raw = source[sig_start:body_start].strip().replace('\n', ' ')
        sig_line_raw = re.sub(r'\s+', ' ', sig_line_raw)

        start_line = source[:sig_start].count('\n') + 1
        body_start_line = source[:body_start].count('\n') + 1

        candidates.append({
            "signature": sig_line_raw,
            "code": code,
            "start_line": start_line,
            "body_start_line": body_start_line,
            "sig_line_masked": sig_line_masked,
        })

    if not candidates:
        return None

    # ★ 优先级 1：hint 行号匹配（JaCoCo `line` 通常指向方法体第一可执行语句，
    # 因此满足 start_line ≤ hint 且 hint 落在方法体范围内）
    if start_line_hint is not None:
        best = None
        best_dist = None
        for c in candidates:
            # hint 应该位于 [body_start_line, body_start_line + <N>] 之间。
            # 取 "hint - body_start_line" 最小但非负的候选为最佳。
            dist = start_line_hint - c["body_start_line"]
            if dist < 0:
                continue
            if best_dist is None or dist < best_dist:
                best = c
                best_dist = dist
        if best is not None:
            return {"signature": best["signature"], "code": best["code"],
                    "start_line": best["start_line"]}

    # 优先级 2：desc 参数匹配
    if desc:
        for c in candidates:
            if _signature_matches_desc(c["sig_line_masked"], desc):
                return {"signature": c["signature"], "code": c["code"],
                        "start_line": c["start_line"]}

    # 优先级 3：取第一个（兜底）
    c = candidates[0]
    return {"signature": c["signature"], "code": c["code"], "start_line": c["start_line"]}


def _pick_from_xml(
    top_k: int = 15,
    min_lines: int = 10,
    max_coverage: float = 95.0,
    max_per_class: int = 2,
    strata: bool = True,
) -> List[Dict]:
    """解析 XML 报告，按规则挑选候选方法。

    Args:
        strata: 是否按覆盖率分层采样（70% 置信）。论文场景下必开，
                以同时证明方法论对低/中/高覆盖率方法都有提升。
    """
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

            # ★ 关键：统计每个方法名在 XML 里出现的次数；
            #   同名重载（name_count > 1）目前不能被 evaluator 的
            #   get_method_coverage(name) 精确区分，为避免"测的重载 A、
            #   统计的重载 B"这种口径错位，直接剔除出候选池。
            name_counts: Dict[str, int] = {}
            for method in cls.findall("method"):
                nm = method.get("name", "")
                if nm in SKIP_METHODS:
                    continue
                name_counts[nm] = name_counts.get(nm, 0) + 1

            # 收集该类的候选方法
            class_candidates: List[Dict] = []
            for method in cls.findall("method"):
                mname = method.get("name", "")
                if mname in SKIP_METHODS:
                    continue
                # 跳过同名重载（方法名在该类内不唯一）
                if name_counts.get(mname, 0) > 1:
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
                # JaCoCo XML 的 line 指向方法体某行（通常是第一条可执行语句所在行），
                # 用它精确区分重载。
                mline = method.get("line")
                try:
                    mline_int = int(mline) if mline is not None else None
                except (TypeError, ValueError):
                    mline_int = None
                snippet = _extract_method_snippet(
                    source, mname, desc=mdesc, start_line_hint=mline_int)
                if snippet is None and mdesc:
                    # descriptor 精确匹配偶尔会因源码签名表达差异失配，做一次降级兜底
                    snippet = _extract_method_snippet(
                        source, mname, desc="", start_line_hint=mline_int)
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

    # 全局排序
    candidates.sort(key=lambda x: -x["value"])

    if not strata:
        return candidates[:top_k]

    # ★ 分层采样：低覆盖率 [0, 30) / 中 [30, 70) / 高 [70, max_coverage)
    #   每档容量大致均分 top_k，不够时用其他档的 high-value 补齐
    low  = [c for c in candidates if c["line_coverage"] < 30]
    mid  = [c for c in candidates if 30 <= c["line_coverage"] < 70]
    high = [c for c in candidates if 70 <= c["line_coverage"] < max_coverage]

    per = max(1, top_k // 3)
    selected: List[Dict] = []
    # 各档取 per 个（本档已经对 value 排序）
    for bucket in (low, mid, high):
        selected.extend(bucket[:per])

    # 如果不足 top_k，用剩余候选的 high-value 顺次补
    chosen_keys = {(c["full_class_name"], c["method_name"]) for c in selected}
    for c in candidates:
        if len(selected) >= top_k:
            break
        k = (c["full_class_name"], c["method_name"])
        if k in chosen_keys:
            continue
        selected.append(c)
        chosen_keys.add(k)

    # 最终按 value 再排一次（输出更有可读性）
    selected.sort(key=lambda x: -x["value"])
    return selected[:top_k]


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
    parser.add_argument("--min-lines", type=int, default=10,
                        help="方法最小总行数（含签名+体），默认 10，保证样本有足够可覆盖点")
    parser.add_argument("--max-coverage", type=float, default=95.0,
                        help="只保留行覆盖率 < 该值(%%) 的方法；默认 95")
    parser.add_argument("--max-per-class", type=int, default=4)
    parser.add_argument("--no-strata", action="store_true",
                        help="禁用分层采样，回过头按 value 纯排名挑前 N 个")
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
        strata=not args.no_strata,
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
