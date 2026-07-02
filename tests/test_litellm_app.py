from litellm_proxy_headroom.litellm_app import _rewrite_anthropic_system_as_user


def test_rewrite_anthropic_system_blocks_into_first_user_message() -> None:
    payload = {
        "model": "gpt-5.4-mini",
        "system": [
            {"type": "text", "text": "follow repo instructions"},
            {"type": "text", "text": "be concise"},
        ],
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "say ok"}],
            }
        ],
    }

    assert _rewrite_anthropic_system_as_user(payload) is True

    assert "system" not in payload
    assert payload["messages"][0] == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "System instructions:\n\nfollow repo instructions\nbe concise",
            },
            {"type": "text", "text": "say ok"},
        ],
    }


def test_rewrite_anthropic_system_string_into_user_string() -> None:
    payload = {
        "model": "gpt-5.5",
        "system": "follow repo instructions",
        "messages": [{"role": "user", "content": "say ok"}],
    }

    assert _rewrite_anthropic_system_as_user(payload) is True

    assert payload["messages"] == [
        {
            "role": "user",
            "content": "System instructions:\n\nfollow repo instructions\n\nsay ok",
        }
    ]


def test_rewrite_anthropic_model_aliases_for_chatgpt_backend() -> None:
    payload = {
        "model": "claude-sonnet-5",
        "system": "native anthropic system prompt",
        "messages": [{"role": "user", "content": "say ok"}],
    }

    assert _rewrite_anthropic_system_as_user(payload) is True

    assert "system" not in payload
    assert payload["messages"][0]["content"].startswith("System instructions:")


def test_rewrite_leaves_non_chatgpt_aliases_unchanged() -> None:
    payload = {
        "model": "native-anthropic-model",
        "system": "native anthropic system prompt",
        "messages": [{"role": "user", "content": "say ok"}],
    }

    assert _rewrite_anthropic_system_as_user(payload) is False

    assert payload["system"] == "native anthropic system prompt"
