"""
project_config.py —— 多项目配置加载器

所有需要访问"被测项目"信息的脚本（pick_methods / run_batch / evaluator）
都通过这里读取配置，避免到处 hard-code "gson"。

核心函数：
    load_project(name=None) -> ProjectConfig
        name 为空时读取 data/projects.yaml 中 active 指定的那个。

环境变量 UTG_PROJECT 可覆盖 active（方便在 shell 里临时切换）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_YAML = ROOT / "data" / "projects.yaml"


@dataclass
class ProjectConfig:
    """单个被测项目的所有路径/构建信息。"""
    name: str
    project_dir: str           # Maven 项目根目录
    module_name: Optional[str] # Maven 子模块名；None 表示单模块
    java_home: str
    baseline_exec: str
    rag_index: str
    surefire_arglines: bool = False  # pom 是否显式引用了 <argLine>${argLine}</argLine>
    # 额外的 Maven 参数。例如 commons-lang 需要 -Drat.skip=true 绕开 apache-rat-plugin
    # 的 License 头检查（LLM 生成的测试文件默认没有 ASF License 头）。所有调用 mvn
    # 的阶段（pick / compile / run_test / coverage）都会在 cmd 末尾追加这些参数。
    mvn_extra_args: list = None
    # 目标项目使用的 JUnit 主版本：4 或 5。生成器/修复器据此选择正确的 import、注解、断言 API。
    junit_version: int = 4

    # 下面是派生/衍生字段，__post_init__ 里自动算
    module_dir: str = ""       # 模块根目录（= project_dir/module_name 或 project_dir）
    src_main_java: str = ""
    src_test_java: str = ""
    target_classes: str = ""

    def __post_init__(self):
        if self.mvn_extra_args is None:
            self.mvn_extra_args = []
        if self.module_name:
            self.module_dir = os.path.join(self.project_dir, self.module_name)
        else:
            self.module_dir = self.project_dir
        self.src_main_java = os.path.join(self.module_dir, "src/main/java")
        self.src_test_java = os.path.join(self.module_dir, "src/test/java")
        self.target_classes = os.path.join(self.module_dir, "target/classes")

    def mvn_module_args(self) -> list:
        """返回构建命令需要追加的 -pl/-am 参数（单模块时为空）。"""
        if self.module_name:
            return ["-pl", self.module_name, "-am"]
        return []

    def jacoco_mvn_flags(self, jacoco_agent: str) -> list:
        """根据 surefire_arglines 生成正确的 JaCoCo 注入参数。

        - 若 pom.xml 中 <argLine>${argLine}</argLine>：只能用 -DargLine，否则重复注入会让 JVM 崩
        - 否则：什么都不追加（由调用者通过 JAVA_TOOL_OPTIONS 注入）
        """
        if self.surefire_arglines:
            return [f"-DargLine={jacoco_agent}"]
        return []

    def jacoco_tool_options(self, jacoco_agent: str) -> Optional[str]:
        """返回需要设置的 JAVA_TOOL_OPTIONS 值，若不适合用则返回 None。"""
        if self.surefire_arglines:
            return None
        return jacoco_agent

    def build_env(self, extra: Optional[dict] = None) -> dict:
        """构造一个带正确 JAVA_HOME / PATH 的 env 字典。"""
        env = os.environ.copy()
        env["JAVA_HOME"] = self.java_home
        env["M2_HOME"] = "/opt/maven-new"
        env["PATH"] = f"{self.java_home}/bin:/opt/maven-new/bin:{env.get('PATH', '')}"
        if extra:
            env.update(extra)
        return env


def load_project(name: Optional[str] = None) -> ProjectConfig:
    """加载项目配置。

    优先级：
        1. 参数 name
        2. 环境变量 UTG_PROJECT
        3. projects.yaml 的 active
    """
    if not PROJECTS_YAML.exists():
        raise FileNotFoundError(
            f"项目配置文件不存在: {PROJECTS_YAML}. 请参考仓库根的 data/projects.yaml")

    with open(PROJECTS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    projects = data.get("projects") or {}
    if not projects:
        raise ValueError(f"{PROJECTS_YAML} 中没有定义任何 projects")

    target_name = name or os.environ.get("UTG_PROJECT") or data.get("active")
    if not target_name:
        raise ValueError(
            "未指定 active 项目：请在 projects.yaml 中设置 active，或传入 name/UTG_PROJECT。")

    if target_name not in projects:
        raise KeyError(
            f"项目 {target_name!r} 不存在，可选：{sorted(projects.keys())}")

    cfg = projects[target_name]
    return ProjectConfig(
        name=target_name,
        project_dir=cfg["project_dir"],
        module_name=cfg.get("module_name"),  # 允许 null
        java_home=cfg.get("java_home", "/usr/lib/jvm/java-17-openjdk"),
        baseline_exec=cfg.get("baseline_exec", f"/tmp/{target_name}-jacoco.exec"),
        rag_index=cfg.get("rag_index", f"/tmp/{target_name}_code_rag.index"),
        surefire_arglines=bool(cfg.get("surefire_arglines", False)),
        mvn_extra_args=list(cfg.get("mvn_extra_args") or []),
        junit_version=int(cfg.get("junit_version", 4)),
    )


def list_projects() -> list:
    """列出所有可用的项目名（用于 CLI --list-projects）。"""
    if not PROJECTS_YAML.exists():
        return []
    with open(PROJECTS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return sorted((data.get("projects") or {}).keys())
