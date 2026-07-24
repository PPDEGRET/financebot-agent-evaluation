from datetime import datetime, timezone

from myaibot.market_context.entity_extract import extract_mentions_from_document, load_entity_index
from myaibot.market_context.schema import MarketContextDocument


def test_extract_mentions_prefers_cashtags_and_known_tickers(tmp_path):
    universe = tmp_path / "universe.csv"
    universe.write_text(
        "symbol,name\nTSLA,Tesla Inc.\nAI,C3.ai Inc.\nA,Agilent Technologies Inc.\nON,ON Semiconductor Corp.\n",
        encoding="utf-8",
    )
    index = load_entity_index([universe])
    doc = MarketContextDocument(
        document_id="vdoc_test",
        source_id="youtube_test",
        source_type="youtube",
        source_name="Test",
        title="Tesla and $AI market update",
        text="TSLA looks strong. A is just an article. ON is noisy unless cashtagged. Tesla has upside.",
        published_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        available_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )

    mentions = extract_mentions_from_document(doc, index)
    symbols = {mention.symbol for mention in mentions}

    assert "TSLA" in symbols
    assert "AI" in symbols  # allowed because it was written as $AI
    assert "A" not in symbols
    assert "ON" not in symbols
    assert any(mention.company_name == "Tesla Inc." for mention in mentions)
