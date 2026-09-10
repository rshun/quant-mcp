"""读取并校验 Spring ETL pipeline 契约。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


PIPELINE_PATH_ENV = "SPRING_PIPELINE_PATH"


def _pipeline_path() -> Path:
    raw_path = os.environ.get(PIPELINE_PATH_ENV, "").strip()
    if not raw_path:
        raise RuntimeError(
            f"环境变量 {PIPELINE_PATH_ENV} 未设置："
            "请指定 Spring config/pipeline.yaml 的完整路径。"
        )
    return Path(raw_path).expanduser()


def _validate_pipeline(data: Any, source: Path) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict) or not data:
        raise ValueError(f"pipeline 配置必须是非空映射: {source}")

    normalized: dict[str, dict[str, Any]] = {}
    for program, spec in data.items():
        if not isinstance(program, str) or not program.strip():
            raise ValueError(f"pipeline 程序名必须是非空字符串: {source}")
        if not isinstance(spec, dict):
            raise ValueError(f"pipeline.{program} 必须是映射: {source}")

        requires = spec.get("requires")
        if not isinstance(requires, list) or not all(
            isinstance(dep, str) and dep.strip() for dep in requires
        ):
            raise ValueError(
                f"pipeline.{program}.requires 必须是非空字符串组成的列表: {source}"
            )

        desc = spec.get("desc")
        if not isinstance(desc, str) or not desc.strip():
            raise ValueError(f"pipeline.{program}.desc 必须是非空字符串: {source}")

        normalized[program] = {
            "requires": list(requires),
            "desc": desc.strip(),
        }

    _validate_acyclic(normalized, source)
    return normalized


def _validate_acyclic(
    pipeline: dict[str, dict[str, Any]], source: Path
) -> None:
    """只对配置内程序判环；sync_capital 等外部前置不参与拓扑图。"""
    graph = {
        program: [dep for dep in spec["requires"] if dep in pipeline]
        for program, spec in pipeline.items()
    }
    resolved: set[str] = set()
    while True:
        ready = [
            program
            for program, requires in graph.items()
            if program not in resolved and all(dep in resolved for dep in requires)
        ]
        if not ready:
            break
        resolved.update(ready)

    unresolved = set(graph) - resolved
    if unresolved:
        raise ValueError(
            f"pipeline 存在循环依赖 {sorted(unresolved)}: {source}"
        )


def load_pipeline() -> tuple[dict[str, dict[str, Any]], Path]:
    """从显式路径读取 Spring pipeline，不猜测兄弟仓库位置。"""
    source = _pipeline_path()
    if not source.is_file():
        raise FileNotFoundError(f"Spring pipeline 配置不存在: {source}")

    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Spring pipeline YAML 解析失败: {source}") from exc

    return _validate_pipeline(data, source), source
