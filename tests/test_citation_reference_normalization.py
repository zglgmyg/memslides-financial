from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from memslides.integrations.research_report import citation_reference_normalization as subject


def _source(cite_id: str, title: str) -> dict[str, str]:
    return {
        "cite_id": cite_id,
        "description": f"网页发布时间：2025-08-20 {title}",
        "source_domain": "example.com",
    }


def test_missing_model_reference_uses_source_only_fallback() -> None:
    sources = [_source("web-1", "第一条原文标题"), _source("web-2", "第二条原文标题及摘要")]
    content = json.dumps({
        "references": [{
            "cite_id": "web-1", "title": "第一条原文标题",
            "publisher": "", "document_number": "",
        }]
    }, ensure_ascii=False)

    parsed = subject._parse_references(content, sources)

    assert parsed["web-2"] == {
        "reference_title": "第二条原文标题及摘要",
        "reference_publisher": "",
        "reference_document_number": "",
    }


def test_reference_normalization_cache_avoids_repeated_model_request(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[int] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(1)
            request = json.loads(kwargs["messages"][1]["content"])
            rows = [
                {"cite_id": row["cite_id"], "title": row["raw_description"].split()[-1], "publisher": "", "document_number": ""}
                for row in request["references"]
            ]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"references": rows}, ensure_ascii=False)))]
            )

    class FakeOpenAI:
        def __init__(self, **_):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(subject, "OpenAI", FakeOpenAI)
    cache = tmp_path / "normalization-cache.json"
    catalog = {"web-1": _source("web-1", "缓存标题")}

    first = subject.normalize_reference_catalog(catalog, api_key="test", cache_path=cache)
    second = subject.normalize_reference_catalog(catalog, api_key="test", cache_path=cache)

    assert first == second
    assert calls == [1]
    assert cache.is_file()
