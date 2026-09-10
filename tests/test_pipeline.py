"""Spring pipeline 配置读取契约。"""

from pathlib import Path

import pytest

from quant_mcp.pipeline import load_pipeline


VALID_PIPELINE = """
import_daily:
  requires: []
  desc: 股票日线入库
fill_shares:
  requires: [import_daily]
  desc: 回填股本
"""


def test_load_pipeline_from_explicit_path(tmp_path, monkeypatch):
    source = tmp_path / "pipeline.yaml"
    source.write_text(VALID_PIPELINE, encoding="utf-8")
    monkeypatch.setenv("SPRING_PIPELINE_PATH", str(source))

    pipeline, actual_source = load_pipeline()

    assert actual_source == source
    assert pipeline["fill_shares"]["requires"] == ["import_daily"]


def test_pipeline_path_is_required(monkeypatch):
    monkeypatch.delenv("SPRING_PIPELINE_PATH", raising=False)
    with pytest.raises(RuntimeError, match="SPRING_PIPELINE_PATH"):
        load_pipeline()


def test_pipeline_rejects_invalid_requires(tmp_path, monkeypatch):
    source = tmp_path / "pipeline.yaml"
    source.write_text(
        "import_daily:\n  requires: import_base\n  desc: 股票日线入库\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SPRING_PIPELINE_PATH", str(source))

    with pytest.raises(ValueError, match="requires"):
        load_pipeline()


def test_pipeline_rejects_cycle(tmp_path, monkeypatch):
    source = tmp_path / "pipeline.yaml"
    source.write_text(
        "first:\n  requires: [second]\n  desc: first\n"
        "second:\n  requires: [first]\n  desc: second\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SPRING_PIPELINE_PATH", str(source))

    with pytest.raises(ValueError, match="循环依赖"):
        load_pipeline()


def test_get_etl_pipeline_tool(srv, tmp_path, monkeypatch):
    source = tmp_path / "pipeline.yaml"
    source.write_text(VALID_PIPELINE, encoding="utf-8")
    monkeypatch.setenv("SPRING_PIPELINE_PATH", str(source))

    result = srv.get_etl_pipeline()

    assert result["source"] == str(source)
    assert result["program_count"] == 2
    assert result["programs"]["import_daily"]["requires"] == []
