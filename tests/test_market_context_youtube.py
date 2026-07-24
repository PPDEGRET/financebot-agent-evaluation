from myaibot.market_context.ingest_youtube import extract_video_id, normalize_video_url, parse_json3_transcript, target_from_config_item


def test_extract_video_id_common_url_shapes():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert normalize_video_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_target_from_config_dict():
    target = target_from_config_item({"url": "https://youtu.be/dQw4w9WgXcQ", "published_at": "2025-01-01", "title": "x"})
    assert target is not None
    assert target.video_id == "dQw4w9WgXcQ"
    assert target.title == "x"
    assert target.published_at is not None


def test_parse_json3_transcript():
    snippets = parse_json3_transcript('{"events":[{"tStartMs":1500,"dDurationMs":2100,"segs":[{"utf8":"Hello"},{"utf8":" world"}]}]}')
    assert snippets == [{"text": "Hello world", "start": 1.5, "duration": 2.1}]
