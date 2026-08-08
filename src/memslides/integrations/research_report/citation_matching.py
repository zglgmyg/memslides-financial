"""Match final HTML claims to citation units with DeepSeek."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from openai import OpenAI


DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"


_SYSTEM_PROMPT = """你只负责判断 PPT 观点是否被候选研报引用单元充分支持。
对每个 html_claim 必须输出一个结果。只有候选单元直接、充分支持观点时才标记 supported；否则标记 unsupported。
不得改写观点，不得生成新的 ID、来源名称、网址或解释。citation_unit_ids 只能从输入候选 ID 中选择。
只返回以下 JSON 对象：
{"mappings":[{"html_claim_id":"...","status":"supported|unsupported","citation_unit_ids":["..."]}]}"""


def _parse_mapping(
    content: str,
    html_claims: Sequence[Mapping[str, Any]],
    candidate_units: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {"mappings"}:
        raise ValueError("DeepSeek output must contain only mappings")

    claim_ids = [str(claim["html_claim_id"]) for claim in html_claims]
    candidate_ids = {str(unit["unit_id"]) for unit in candidate_units}
    mappings_by_claim: dict[str, dict[str, Any]] = {}
    for mapping in value["mappings"]:
        if not isinstance(mapping, dict) or set(mapping) != {
            "html_claim_id",
            "status",
            "citation_unit_ids",
        }:
            raise ValueError("DeepSeek returned an invalid mapping")
        claim_id = str(mapping["html_claim_id"])
        status = mapping["status"]
        unit_ids = mapping["citation_unit_ids"]
        if claim_id not in claim_ids or claim_id in mappings_by_claim:
            raise ValueError("DeepSeek returned an unknown or duplicate claim ID")
        if status not in {"supported", "unsupported"} or not isinstance(unit_ids, list):
            raise ValueError("DeepSeek returned an invalid mapping status")
        if any(str(unit_id) not in candidate_ids for unit_id in unit_ids):
            raise ValueError("DeepSeek returned a non-candidate citation unit ID")
        unit_ids = [str(unit_id) for unit_id in unit_ids]
        if (status == "supported") != bool(unit_ids):
            raise ValueError("DeepSeek returned an inconsistent mapping status")
        mappings_by_claim[claim_id] = {
            "html_claim_id": claim_id,
            "status": status,
            "citation_unit_ids": unit_ids,
        }

    if set(mappings_by_claim) != set(claim_ids):
        raise ValueError("DeepSeek did not return every HTML claim")
    return [mappings_by_claim[claim_id] for claim_id in claim_ids]


def judge_claim_citations(
    html_claims: Sequence[Mapping[str, Any]],
    candidate_units: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120,
) -> list[dict[str, Any]]:
    """Ask DeepSeek to map HTML claims to the supplied citation units."""

    if not candidate_units:
        return [
            {
                "html_claim_id": str(claim["html_claim_id"]),
                "status": "unsupported",
                "citation_unit_ids": [],
            }
            for claim in html_claims
        ]

    request_input = {
        "html_claims": [
            {"id": claim["html_claim_id"], "text": claim["text"]}
            for claim in html_claims
        ],
        "candidate_units": [
            {"id": unit["unit_id"], "text": unit["text"]}
            for unit in candidate_units
        ],
    }
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(request_input, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("DeepSeek returned empty content")
    return _parse_mapping(content, html_claims, candidate_units)
