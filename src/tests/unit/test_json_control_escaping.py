import json

import pytest

from src.llms.litellm_provider import _escape_json_controls


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x1f"])
def test_escaping_preserves_string_contents(control):
    text = f"before{control}after"
    written = '{"text":"' + text + '"}'
    escaped = _escape_json_controls(written)
    assert json.loads(escaped) == {"text": text}


def test_valid_json_is_unchanged():
    written = '{ "text": "é\\nline", "number": 3 }'
    assert _escape_json_controls(written) == written


@pytest.mark.parametrize(
    "written",
    ['{"text":"unfinished', '{"text": missing}', '{"text":"raw\nline",}'],
)
def test_other_syntax_errors_are_rejected(written):
    with pytest.raises(json.JSONDecodeError):
        _escape_json_controls(written)
