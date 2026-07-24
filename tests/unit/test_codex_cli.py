from myaibot.agents.codex_cli import extract_first_json_object


def test_extract_first_json_object_from_fenced_output():
    text = 'hello\n```json\n{"summary":"ok","confidence":0.5}\n```'
    assert extract_first_json_object(text) == {"summary": "ok", "confidence": 0.5}
