from __future__ import annotations

import json
from pathlib import Path

from memslides.tools import structured_visuals as sv


def test_table_asset_emits_svg_metadata_and_cjk_raster_hints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sv, "vlc", None)

    result = sv.render_table_asset_impl(
        rows=[
            {
                "指标": "长文本指标说明需要自动换行以避免表格溢出",
                "Score": "91.2",
                "Evidence": "Native text plus raster-safe SVG fallback.",
            },
            {"指标": "鲁棒性", "Score": "88.0", "Evidence": "Revision rounds completed."},
        ],
        columns=["指标", "Score", "Evidence"],
        caption="中文评估表",
        footnote="Generated for OSS structured visual parity.",
        output_mode="both",
        workspace=tmp_path,
    )

    assert result["kind"] == "table"
    assert result["contains_cjk"] is True
    assert result["visual_type"] == "table"
    assert result["preferred_pptx_export"] == "raster"
    assert result["recommended_width"] == 1120
    assert result["layout"]["body_size"] >= 18
    assert result["recommended_height"] > 120
    assert result["layout"]["wrapped_cells"]["指标"] >= 1
    assert result["rendered_paths"]["svg"].endswith(".svg")
    assert result["primary_path"].endswith(".svg")
    assert result["warnings"]
    svg_text = Path(result["svg_path"]).read_text(encoding="utf-8")
    assert 'data-visual-kind="table"' in svg_text
    assert 'data-preferred-pptx-export="raster"' in svg_text
    meta = json.loads(Path(result["meta_path"]).read_text(encoding="utf-8"))
    assert meta["contains_cjk"] is True
    assert meta["preferred_pptx_export"] == "raster"


def test_flowchart_asset_supports_labeled_edges_and_layout_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sv, "vlc", None)

    result = sv.render_flowchart_asset_impl(
        nodes=["输入", "Encode", "Score", "Review", "Output"],
        edges=[
            "输入 -- raw text -> Encode",
            "Encode -- attention -> Score",
            "Score -- inspect -> Review",
            "Review -- accepted -> Output",
        ],
        diagram_kind="pipeline",
        title="修订管线",
        output_format="both",
        workspace=tmp_path,
    )

    assert result["kind"] == "flowchart"
    assert result["edge_labels"] == ["raw text", "attention", "inspect", "accepted"]
    assert result["contains_cjk"] is True
    assert result["visual_type"] == "flowchart"
    assert result["preferred_pptx_export"] == "raster"
    assert result["recommended_width"] >= 760
    assert result["recommended_height"] >= 520
    assert result["primary_path"].endswith(".svg")
    assert result["layout"]["uses_edges"] is True
    assert result["layout"]["edge_routes"]
    assert "rank" in result["layout"]["node_bounds"]["Encode"]
    svg_text = Path(result["svg_path"]).read_text(encoding="utf-8")
    assert 'data-visual-kind="flowchart"' in svg_text
    assert "attention" in svg_text


def test_chart_moves_titles_over_20_visible_characters_outside_canvas(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sv, "vlc", None)
    long_title = "**这是超过二十个可见字符的分析性结论应交给设计师排版**"

    result = sv.render_chart_asset_impl(
        chart_type="line",
        rows=[{"年份": "2024A", "销量": 210}, {"年份": "2025E", "销量": 450}],
        x_field="年份",
        y_fields=["销量"],
        title=long_title,
        output_format="svg",
        workspace=tmp_path,
    )

    svg_text = Path(result["svg_path"]).read_text(encoding="utf-8")
    meta = json.loads(Path(result["meta_path"]).read_text(encoding="utf-8"))
    assert result["chart_title"] == ""
    assert result["external_caption"] == long_title
    assert long_title not in svg_text
    assert meta["external_caption"] == long_title


def test_chart_keeps_short_title_inside_canvas(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sv, "vlc", None)
    short_title = "产销量预测"

    result = sv.render_chart_asset_impl(
        chart_type="line",
        rows=[{"年份": "2024A", "销量": 210}, {"年份": "2025E", "销量": 450}],
        x_field="年份",
        y_fields=["销量"],
        title=short_title,
        output_format="svg",
        workspace=tmp_path,
    )

    assert result["chart_title"] == short_title
    assert result["external_caption"] == ""
    assert short_title in Path(result["svg_path"]).read_text(encoding="utf-8")


def test_chart_spec_uses_audience_labels_and_financial_defaults() -> None:
    spec = sv.build_chart_spec(
        chart_type="line",
        source_rows=[
            {"category": "2023A", "收入": 29136},
            {"category": "2024A", "收入": 32983},
            {"category": "2025E", "收入": 51494},
        ],
        x_field="category",
        y_fields=["收入"],
        x_label="期间",
        y_label="百万元",
    )

    assert spec["encoding"]["x"]["title"] == "期间"
    assert spec["encoding"]["x"]["sort"] == ["2023A", "2024A", "2025E"]
    assert spec["encoding"]["y"]["title"] == "百万元"
    assert spec["encoding"]["y"]["scale"] == {"zero": False}
    assert spec["encoding"]["y"]["axis"]["format"] == ",.0f"
    assert spec["mark"]["strokeWidth"] == 3
    assert "line" not in spec["mark"]


def test_chart_spec_hides_internal_field_names() -> None:
    spec = sv.build_chart_spec(
        chart_type="grouped_bar",
        source_rows=[
            {"category": "A", "收入": 10, "利润": 2},
            {"category": "B", "收入": 12, "利润": 3},
        ],
        x_field="category",
        y_fields=["收入", "利润"],
    )

    assert spec["encoding"]["x"]["title"] is None
    assert spec["encoding"]["y"]["title"] is None
    assert spec["encoding"]["color"]["legend"]["title"] is None
    tooltip_titles = [item["title"] for item in spec["encoding"]["tooltip"]]
    assert tooltip_titles == ["类别", "数值", "系列"]


def test_bar_chart_uses_horizontal_layout_for_long_category_labels() -> None:
    spec = sv.build_chart_spec(
        chart_type="bar",
        source_rows=[
            {"category": "内蒙古项目新增烯烃产能", "产能": 300},
            {"category": "宁东基地现有烯烃产能", "产能": 220},
        ],
        x_field="category",
        y_fields=["产能"],
        x_label="项目",
        y_label="万吨/年",
    )

    assert spec["encoding"]["x"]["field"] == "产能"
    assert spec["encoding"]["x"]["scale"] == {"zero": True}
    assert spec["encoding"]["y"]["field"] == "category"
    assert spec["encoding"]["y"]["axis"]["labelLimit"] == 260


def test_pie_chart_adds_readable_value_labels() -> None:
    spec = sv.build_chart_spec(
        chart_type="pie",
        source_rows=[
            {"业务": "烯烃", "占比": 62},
            {"业务": "焦炭", "占比": 23},
            {"业务": "精细化工", "占比": 15},
        ],
        x_field="业务",
        y_fields=["占比"],
        y_label="%",
    )

    assert "encoding" not in spec
    assert spec["layer"][0]["mark"]["strokeWidth"] == 2
    assert spec["layer"][1]["encoding"]["text"]["field"] == "__display_value__"
    assert spec["layer"][1]["transform"][0]["calculate"].endswith("+ '%'")


def test_render_table_asset_accepts_aliases_and_routes_chart_like_calls(tmp_path: Path, monkeypatch) -> None:
    from memslides.tools import asset_services

    captured: dict[str, object] = {}

    def fake_chart_renderer(**kwargs):
        captured.update(kwargs)
        return {
            "kind": "chart",
            "warnings": [],
            "rendered_paths": {"svg": str(tmp_path / "chart.svg")},
            "primary_path": str(tmp_path / "chart.svg"),
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset_services, "render_chart_asset_impl", fake_chart_renderer)

    result = asset_services.render_table_asset(
        rows=[{"epoch": "1", "accuracy": "0.91"}],
        chart_type="line",
        x_field="epoch",
        y_fields="accuracy",
        title="Accuracy Trend",
        output_format="both",
    )

    assert result["kind"] == "chart"
    assert result["warnings"] == [
        "render_table_asset received chart-like arguments and routed to render_chart_asset."
    ]
    assert captured["chart_type"] == "line"
    assert captured["x_field"] == "epoch"
    assert captured["y_fields"] == ["accuracy"]
    assert captured["title"] == "Accuracy Trend"
    assert captured["output_format"] == "both"
    assert captured["workspace"] == tmp_path


def test_render_table_asset_title_and_output_format_aliases(tmp_path: Path, monkeypatch) -> None:
    from memslides.tools import asset_services

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sv, "vlc", None)

    result = asset_services.render_table_asset(
        rows=[{"A": "甲", "B": "1"}],
        columns=["A", "B"],
        title="Alias Caption",
        output_format="svg",
    )

    assert result["kind"] == "table"
    assert result["title"] == "Alias Caption"
    assert result["requested_output_mode"] == "svg"
    assert result["svg_path"]
    assert result["contains_cjk"] is True
