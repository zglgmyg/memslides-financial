"""Normalize web reference metadata with constrained DeepSeek extraction."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from openai import OpenAI

from .citation_appendix import format_citation_source
from .citation_matching import DEFAULT_BASE_URL, DEFAULT_MODEL


_BATCH_SIZE = 20
_SYSTEM_PROMPT = """你只负责从 PDF 附录的网页来源描述中抽取引用字段，不得改写、补充或猜测。
对每条输入返回且只返回：cite_id、title、publisher、document_number。
如果描述中存在明确标题，title 应提取该标题，并排除标题后的站点名称、栏目名称和正文摘要。
如果描述中不存在明确标题，title 应根据描述中明确出现的主体、报告期、事件或数据主题生成简短的描述性标题，并使用“相关报道”“数据页面”或“互动问答”等措辞，不得添加描述中没有出现的公司、日期、数字、公告编号或结论。
publisher 是描述中明确出现的发布机构或媒体名称；没有则返回空字符串。
document_number 是描述中明确出现的公告编号；没有则返回空字符串。
publisher、document_number 的每个非空值都必须是 raw_description 中逐字连续出现的原文片段。
只返回以下 JSON 对象，不得附加解释：
{"references":[{"cite_id":"...","title":"...","publisher":"...","document_number":"..."}]}"""


def _parse_references(
    content: str,
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {"references"}:
        raise ValueError("DeepSeek output must contain only references")

    descriptions = {
        str(source["cite_id"]): str(source.get("description", ""))
        for source in sources
    }
    references: dict[str, dict[str, str]] = {}
    for reference in value["references"]:
        if not isinstance(reference, dict) or set(reference) != {
            "cite_id",
            "title",
            "publisher",
            "document_number",
        }:
            raise ValueError("DeepSeek returned an invalid reference")
        cite_id = str(reference["cite_id"])
        if cite_id not in descriptions or cite_id in references:
            raise ValueError("DeepSeek returned an unknown or duplicate cite ID")

        title = reference["title"]
        if not isinstance(title, str):
            raise ValueError("DeepSeek returned a non-string title")
        if not title:
            raise ValueError("DeepSeek returned an empty title")

        fields = {"title": title}
        for field in ("publisher", "document_number"):
            field_value = reference[field]
            if not isinstance(field_value, str):
                raise ValueError(f"DeepSeek returned a non-string {field}")
            if field_value and field_value not in descriptions[cite_id]:
                raise ValueError(f"DeepSeek returned a non-source {field}")
            fields[field] = field_value

        references[cite_id] = {
            "reference_title": fields["title"],
            "reference_publisher": fields["publisher"],
            "reference_document_number": fields["document_number"],
        }

    if set(references) != set(descriptions):
        raise ValueError("DeepSeek did not return every reference")
    return references


def normalize_reference_catalog(
    source_catalog: Mapping[str, Mapping[str, Any]],
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120,
) -> dict[str, dict[str, Any]]:
    """Extract web-reference fields and deterministically format all sources."""

    normalized = {
        cite_id: dict(source) for cite_id, source in source_catalog.items()
    }
    web_sources = [
        source
        for source in normalized.values()
        if str(source.get("description", "")).startswith("网页发布时间：")
    ]
    if web_sources:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        for start in range(0, len(web_sources), _BATCH_SIZE):
            batch = web_sources[start : start + _BATCH_SIZE]
            request_input = {
                "references": [
                    {
                        "cite_id": source["cite_id"],
                        "raw_description": source.get("description", ""),
                    }
                    for source in batch
                ]
            }
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(request_input, ensure_ascii=False),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek returned empty content")
            for cite_id, fields in _parse_references(content, batch).items():
                normalized[cite_id].update(fields)

    for source in normalized.values():
        source["citation_text"] = format_citation_source(source)
    return normalized
