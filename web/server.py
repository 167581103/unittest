"""
Web API Server - Wraps the pipeline into HTTP endpoints with WebSocket progress.
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import CodeRAG, AgenticRAG
from llm import generate_test, chat, analyze_method, PROMPTS
from evaluation.evaluator import TestEvaluator, print_report

app = FastAPI(title="UT-Gen Dashboard")


# ======================== Models ========================


class ProjectConfig(BaseModel):
    project_dir: str  # Maven project root, e.g. /data/workspace/unittest/data/project/gson
    module: str = ""  # Sub-module, e.g. "gson"
    index_path: str = "/tmp/code_rag.index"


class RunRequest(BaseModel):
    project: ProjectConfig
    target_class: str  # Simple name, e.g. "JsonReader"
    full_class_name: str  # e.g. "com.google.gson.stream.JsonReader"
    method_signature: str  # e.g. "public long nextLong() throws IOException"
    method_code: str
    baseline_test: str = ""  # e.g. "JsonReaderTest"
    jacoco_home: str = "/data/workspace/unittest/lib"


class ScanRequest(BaseModel):
    project: ProjectConfig


# ======================== State ========================


class RunState:
    """Holds the state of a single pipeline run for WebSocket broadcasting."""

    def __init__(self):
        self.steps: List[Dict[str, Any]] = []
        self.ws_clients: List[WebSocket] = []

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self.ws_clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.remove(ws)

    async def log(self, step: str, event: str, data: Any = None):
        entry = {"step": step, "event": event, "data": data, "ts": datetime.now().isoformat()}
        self.steps.append(entry)
        await self.broadcast(entry)
        # Force yield event loop to allow WebSocket messages to be sent immediately
        await asyncio.sleep(0)

    def summary(self) -> List[dict]:
        return self.steps


_run_state = RunState()


# ======================== Helpers ========================


def _resolve_source_dir(project: ProjectConfig) -> str:
    """Resolve the main Java source directory."""
    base = project.project_dir
    if project.module:
        base = os.path.join(base, project.module)
    return os.path.join(base, "src/main/java")


def _resolve_test_dir(project: ProjectConfig) -> str:
    base = project.project_dir
    if project.module:
        base = os.path.join(base, project.module)
    return os.path.join(base, "src/test/java")


def _scan_java_classes(source_dir: str) -> List[dict]:
    """Scan Java source directory for classes and their methods."""
    from rag.tree_parser import JavaParser
    parser = JavaParser()
    classes = []
    java_files = list(Path(source_dir).rglob("*.java"))
    for jf in java_files:
        blocks, cls_info = parser.parse_file(str(jf))
        if cls_info is None:
            continue
        methods = []
        for m in cls_info.methods:
            methods.append({
                "name": m.get("name", ""),
                "signature": m.get("signature", ""),
                "return_type": m.get("return_type", ""),
            })
        classes.append({
            "name": cls_info.name,
            "package": cls_info.package,
            "full_name": f"{cls_info.package}.{cls_info.name}" if cls_info.package else cls_info.name,
            "file": str(jf),
            "method_count": len(methods),
            "methods": methods,
        })
    classes.sort(key=lambda c: c["full_name"])
    return classes


def _find_method_code(filepath: str, method_name: str) -> Optional[str]:
    """Extract the full source code of a method from a Java file."""
    from rag.tree_parser import JavaParser
    parser = JavaParser()
    blocks, _ = parser.parse_file(filepath)
    for b in blocks:
        if b.type == "method" and method_name in b.signature:
            return b.code
    return None


# ======================== Routes ========================


@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.post("/api/scan")
async def scan_project(req: ScanRequest):
    """Scan a Maven project to list classes and methods."""
    source_dir = _resolve_source_dir(req.project)
    if not os.path.isdir(source_dir):
        return JSONResponse({"error": f"Source directory not found: {source_dir}"}, 400)
    classes = _scan_java_classes(source_dir)
    return {"classes": classes, "source_dir": source_dir}


@app.post("/api/method_code")
async def get_method_code(filepath: str = "", method_name: str = ""):
    """Get the full source code of a specific method."""
    if not filepath or not method_name:
        return JSONResponse({"error": "filepath and method_name required"}, 400)
    code = _find_method_code(filepath, method_name)
    if code is None:
        return JSONResponse({"error": "Method not found"}, 404)
    return {"code": code}


def _build_index_sync(source_dir: str, index_path: str, batch_size: int = 50):
    """Synchronous wrapper for build_index to run in thread pool."""
    rag = CodeRAG()
    rag.build_index(source_dir, index_path, batch_size=batch_size)
    return rag

def _load_index_sync(index_path: str):
    """Synchronous wrapper for loading index to run in thread pool."""
    return CodeRAG(index_path)

@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    """Run the full pipeline: index -> retrieve -> generate -> evaluate."""
    global _run_state
    _run_state = RunState()
    state = _run_state

    source_dir = _resolve_source_dir(req.project)
    test_dir = _resolve_test_dir(req.project)
    index_path = req.project.index_path
    result: Dict[str, Any] = {"steps": {}}

    try:
        # ---- Step 1: Build / Load Index ----
        await state.log("index", "start")
        if not os.path.exists(index_path):
            await state.log("index", "building", {"source_dir": source_dir})
            # Run blocking operation in thread pool to not block event loop
            rag = await asyncio.to_thread(_build_index_sync, source_dir, index_path, 50)
            await state.log("index", "done", {"blocks": len(rag.blocks), "classes": len(rag.class_info)})
        else:
            rag = await asyncio.to_thread(_load_index_sync, index_path)
            await state.log("index", "loaded", {"blocks": len(rag.blocks), "classes": len(rag.class_info)})
        result["steps"]["index"] = {
            "blocks": len(rag.blocks),
            "classes": len(rag.class_info),
        }

        # ---- Step 2: Agentic RAG Retrieve ----
        await state.log("retrieve", "start")
        # Create AgenticRAG in thread pool to avoid blocking on index loading
        agentic_rag = await asyncio.to_thread(AgenticRAG, index_path, test_dir, True)

        # Monkey-patch _log to capture agentic rag logs
        rag_logs = []
        original_log = agentic_rag._log

        def patched_log(msg):
            rag_logs.append(msg)
            original_log(msg)

        agentic_rag._log = patched_log

        context = await agentic_rag.retrieve(
            req.method_code,
            cls=req.target_class,
            top_k=3,
            method_signature=req.method_signature,
        )
        await state.log("retrieve", "done", {
            "context_length": len(context),
            "context": context,
            "rag_logs": rag_logs,
        })
        result["steps"]["retrieve"] = {
            "context": context,
            "context_length": len(context),
            "rag_logs": rag_logs,
        }

        # ---- Step 2.5: Baseline Coverage ----
        await state.log("baseline", "start")
        evaluator = TestEvaluator(
            project_dir=req.project.project_dir,
            jacoco_home=req.jacoco_home,
        )
        baseline_test = req.baseline_test or f"{req.target_class}Test"
        # Run blocking operation in thread pool
        baseline_cov = await asyncio.to_thread(
            evaluator.get_baseline_coverage,
            target_class=req.full_class_name,
            baseline_test=baseline_test,
        )
        baseline_data = None
        if baseline_cov:
            baseline_data = {
                "line_coverage": baseline_cov.line_coverage,
                "branch_coverage": baseline_cov.branch_coverage,
                "method_coverage": baseline_cov.method_coverage,
                "covered_lines": baseline_cov.covered_lines,
                "total_lines": baseline_cov.total_lines,
            }
        await state.log("baseline", "done", baseline_data)
        result["steps"]["baseline"] = baseline_data

        # ---- Step 2.5: LLM Method Analysis & Test Case Design ----
        await state.log("analysis", "start")
        method_name = req.method_signature.split("(")[0].split()[-1] if "(" in req.method_signature else req.target_class
        test_class_name = f"{req.target_class}_{method_name}_Test"
        package_name = ".".join(req.full_class_name.split(".")[:-1]) if "." in req.full_class_name else ""

        analysis = await analyze_method(
            class_name=req.target_class,
            method_signature=req.method_signature,
            method_code=req.method_code,
            context=context,
            full_class_name=req.full_class_name,
        )

        analysis_data = {
            "method_understanding": analysis["method_understanding"],
            "coverage_analysis": analysis["coverage_analysis"],
            "test_cases": analysis["test_cases"],
            "test_cases_count": len(analysis["test_cases"]),
        }
        await state.log("analysis", "done", analysis_data)
        result["steps"]["analysis"] = analysis_data

        # ---- Step 3: Generate Test (based on test case design) ----
        await state.log("generate", "start")

        # Build the skeleton prompt that will be sent to LLM (for display only).
        # Actual generation happens inside generate_test() which uses skeleton + per-method strategy.
        skeleton_prompt = PROMPTS["test_skeleton"].format(
            class_name=req.target_class,
            test_class_name=test_class_name,
            full_class_name=req.full_class_name,
            package_name=package_name,
            method_signature=req.method_signature,
            method_code=req.method_code,
            context=context or "No context",
        )

        await state.log("generate", "prompts", {
            "skeleton_prompt": skeleton_prompt,
        })

        output_dir = "/tmp/generated_tests"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{test_class_name}.java")

        gen_result = await generate_test(
            class_name=req.target_class,
            method_signature=req.method_signature,
            method_code=req.method_code,
            output_path=output_path,
            context=context,
            test_class_name=test_class_name,
            full_class_name=req.full_class_name,
            test_cases=analysis["test_cases"],
        )

        generated_code = ""
        if gen_result["success"] and os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as f:
                generated_code = f.read()

        await state.log("generate", "done", {
            "success": gen_result["success"],
            "error": gen_result.get("error"),
            "code": generated_code,
            "output_path": output_path,
        })
        result["steps"]["generate"] = {
            "success": gen_result["success"],
            "error": gen_result.get("error"),
            "code": generated_code,
            "prompts": {"skeleton": skeleton_prompt},
        }

        if not gen_result["success"]:
            result["success"] = False
            result["error"] = gen_result.get("error")
            return result

        # ---- Step 4: Evaluate ----
        await state.log("evaluate", "start")
        # Run blocking operation in thread pool
        report = await asyncio.to_thread(
            evaluator.evaluate,
            test_file=output_path,
            test_class=f"{'.'.join(req.full_class_name.split('.')[:-1])}.{test_class_name}",
            target_class=req.full_class_name,
            target_method=method_name,
            baseline_test=baseline_test,
        )

        eval_data = {
            "compilation_success": report.compilation_success,
            "errors": report.errors,
            "test_results": [
                {"method": t.test_method, "passed": t.passed, "error": t.error_message}
                for t in report.test_results
            ],
            "coverage": None,
            "coverage_change": None,
        }
        if report.coverage:
            eval_data["coverage"] = {
                "line_coverage": report.coverage.line_coverage,
                "branch_coverage": report.coverage.branch_coverage,
                "method_coverage": report.coverage.method_coverage,
                "covered_lines": report.coverage.covered_lines,
                "total_lines": report.coverage.total_lines,
                "method_coverages": [
                    {
                        "method_name": mc.method_name,
                        "line_coverage": mc.line_coverage,
                        "branch_coverage": mc.branch_coverage,
                        "covered_lines": mc.covered_lines,
                        "total_lines": mc.total_lines,
                    }
                    for mc in report.coverage.method_coverages
                ] if report.coverage.method_coverages else [],
            }
        if report.coverage_change:
            eval_data["coverage_change"] = {
                "line_coverage_change": report.coverage_change.get("line_coverage_change", 0),
                "branch_coverage_change": report.coverage_change.get("branch_coverage_change", 0),
                "line_change": report.coverage_change.get("line_change", 0),
            }

        await state.log("evaluate", "done", eval_data)
        result["steps"]["evaluate"] = eval_data

        # ---- Step 5: Report ----
        report_dir = "/tmp/test_reports"
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "evaluation_report.json")

        report_json = {
            "timestamp": datetime.now().isoformat(),
            "target_class": req.full_class_name,
            "target_method": method_name,
            "compilation_success": report.compilation_success,
            "errors": report.errors,
            "baseline_coverage": baseline_data,
            "new_coverage": eval_data["coverage"],
            "coverage_change": eval_data["coverage_change"],
            "generated_code": generated_code,
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2, ensure_ascii=False)

        await state.log("report", "saved", {"path": report_path})

        result["success"] = True
        result["report_path"] = report_path
        return result

    except Exception as e:
        await state.log("error", "fatal", {"error": str(e)})
        return JSONResponse({"success": False, "error": str(e)}, 500)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _run_state.ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in _run_state.ws_clients:
            _run_state.ws_clients.remove(ws)


@app.get("/api/history")
async def get_run_history():
    """Return the step logs of the latest run."""
    return {"steps": _run_state.summary()}


# ======================== Static Files ========================

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ======================== Main ========================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
