from datetime import datetime, timezone

from myaibot.market_context.schema import MarketContextDocument, MarketContextMention, MarketContextSourceRegistryEntry
from myaibot.market_context.store import MarketContextStore


def test_jsonl_store_is_idempotent(tmp_path):
    out = tmp_path / "market_context.duckdb"
    doc = MarketContextDocument(
        document_id="vdoc_1",
        source_id="youtube_test",
        source_type="youtube",
        source_name="YouTube Test",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="TSLA update",
        text="TSLA is mentioned here.",
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        available_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        symbols_mentioned=["TSLA"],
    )
    mention = MarketContextMention(
        mention_id="vmention_1",
        document_id="vdoc_1",
        symbol="TSLA",
        company_name="Tesla Inc.",
        confidence=0.9,
        context_window="TSLA is mentioned here.",
        available_at=doc.available_at,
    )
    source = MarketContextSourceRegistryEntry(
        source_id="youtube_test",
        source_name="YouTube Test",
        source_type="youtube",
        locator="https://www.youtube.com/@example/videos",
    )

    with MarketContextStore(out, backend="jsonl") as store:
        assert store.upsert_source_registry([source]).inserted == 1
        assert store.upsert_documents([doc]).inserted == 1
        assert store.upsert_mentions([mention]).inserted == 1
        assert store.upsert_documents([doc]).updated == 1
        assert store.table_counts() == {
            "market_context_source_registry": 1,
            "market_context_documents": 1,
            "market_context_mentions": 1,
        }
        assert store.counts_by_source()[0]["document_count"] == 1
        assert store.counts_by_symbol()[0]["symbol"] == "TSLA"
        assert store.replace_mentions_for_documents([doc.document_id], []).total == 0
        assert store.table_counts()["market_context_mentions"] == 0
