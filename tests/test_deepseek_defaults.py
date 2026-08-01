from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "src" / "memslides" / "memslides.yaml"
DECK_DESIGNER_ROLE_PATH = ROOT / "src" / "memslides" / "roles" / "DeckDesigner.yaml"


def test_packaged_llm_routes_use_current_deepseek_defaults() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    pro_routes = {
        "research_agent",
        "design_agent",
        "modify_agent",
        "reviewer_agent",
        "long_context_model",
        "vision_model",
    }
    flash_routes = {"fast_model", "balanced_model"}

    for route_name in pro_routes | flash_routes:
        route = config[route_name]
        assert "https://api.deepseek.com" in route["base_url"]
        assert route["api_key"] == "${DEEPSEEK_API_KEY:-missing-api-key}"
        assert route["soft_response_parsing"] is True
        assert route["sampling_parameters"]["extra_body"]["thinking"] == {
            "type": "disabled"
        }

    for route_name in pro_routes:
        assert "deepseek-v4-pro" in config[route_name]["model"]
    for route_name in flash_routes:
        assert "deepseek-v4-flash" in config[route_name]["model"]


def test_deepseek_text_routes_do_not_claim_multimodal_support() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for route_name in {"design_agent", "modify_agent", "reviewer_agent", "vision_model"}:
        assert config[route_name]["is_multimodal"] is False


def test_memory_embeddings_are_local_not_openai_or_deepseek() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    embedding = config["memory"]["embedding"]
    assert "local" in embedding["provider"]
    assert "BAAI/bge-m3" in embedding["model"]
    assert embedding["api_key"] == "${MEMSLIDES_EMBEDDING_API_KEY:-}"
    assert "api.openai.com" not in CONFIG_PATH.read_text(encoding="utf-8")


def test_deck_designer_keeps_thinking_tool_for_long_running_deck_state() -> None:
    role = yaml.safe_load(DECK_DESIGNER_ROLE_PATH.read_text(encoding="utf-8"))

    assert "thinking" in role["include_tools"]
    assert "thinking" not in role["exclude_tools"]
