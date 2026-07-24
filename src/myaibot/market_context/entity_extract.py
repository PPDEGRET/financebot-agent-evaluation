"""Simple v1 ticker/company extraction for market-context documents.

This is intentionally conservative. It prefers a known local universe and avoids
short/common all-caps words unless the author used a cashtag such as `$TSLA`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from myaibot.market_context.schema import MarketContextDocument, MarketContextMention, stable_mention_id

CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])\$([A-Za-z]{1,6})(?![A-Za-z0-9_])")
BARE_TICKER_RE = re.compile(r"(?<![A-Za-z0-9_$.])([A-Z]{1,6})(?![A-Za-z0-9_])")
COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:incorporated|inc|corp(?:oration)?|co(?:mpany)?|ltd|limited|plc|holdings?|group|class\s+[a-z]|common\s+stock)\b\.?",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")

# Bare all-caps finance/video text is noisy. Cashtags still pass through.
COMMON_FALSE_POSITIVE_TICKERS = {
    "A",
    "ABOUT",
    "AI",
    "ALL",
    "AM",
    "AND",
    "ANY",
    "API",
    "ARE",
    "AS",
    "AT",
    "ATH",
    "ATL",
    "ATM",
    "BE",
    "BPS",
    "BY",
    "CAN",
    "CASH",
    "CEO",
    "CFO",
    "COST",
    "CPI",
    "DCF",
    "DID",
    "DIY",
    "DO",
    "DOES",
    "EPS",
    "EQ",
    "ETF",
    "ETFS",
    "EU",
    "EV",
    "EVER",
    "FAQ",
    "FANG",
    "FDA",
    "FED",
    "FOMC",
    "FOR",
    "FROM",
    "GARY",
    "GDP",
    "GET",
    "GO",
    "GOT",
    "GPT",
    "GROW",
    "HAD",
    "HAS",
    "HAVE",
    "HE",
    "HER",
    "HIM",
    "HIS",
    "HOW",
    "IF",
    "IN",
    "IPO",
    "IR",
    "IS",
    "IT",
    "ITS",
    "JUST",
    "KPI",
    "LLM",
    "MA",
    "ME",
    "MOAT",
    "MY",
    "NAV",
    "NEXT",
    "NO",
    "NOT",
    "NOW",
    "NYSE",
    "OF",
    "OFF",
    "ON",
    "OR",
    "OUR",
    "OUT",
    "PMI",
    "QE",
    "QT",
    "ROI",
    "SEC",
    "SHE",
    "SO",
    "THAN",
    "THAT",
    "THE",
    "THEM",
    "THEN",
    "THERE",
    "THEY",
    "THIS",
    "TO",
    "UP",
    "US",
    "USA",
    "USD",
    "WAS",
    "WE",
    "WERE",
    "WHAT",
    "WHEN",
    "WHERE",
    "WHICH",
    "WHO",
    "WHY",
    "WILL",
    "WITH",
    "YOY",
    "YTD",
    "YOU",
    "YOUR",
}

GENERIC_COMPANY_ALIASES = {
    "american",
    "bank",
    "capital",
    "common",
    "city",
    "energy",
    "financial",
    "first",
    "founder",
    "global",
    "group",
    "health",
    "holdings",
    "international",
    "mango",
    "medical",
    "national",
    "new",
    "news",
    "people",
    "pattern",
    "popular",
    "properties",
    "quantum",
    "realty",
    "resources",
    "services",
    "solana",
    "southern",
    "star",
    "stock",
    "systems",
    "technologies",
    "technology",
    "trust",
    "united",
}

BULLISH_WORDS = {"bull", "bullish", "beat", "upside", "breakout", "strong", "growth", "buy", "long", "winner"}
BEARISH_WORDS = {"bear", "bearish", "miss", "downside", "fraud", "short", "sell", "weak", "risk", "bubble"}
MAX_ENTITY_TEXT_CHARS = 50_000


@dataclass(frozen=True)
class EntityRecord:
    symbol: str
    company_name: str = ""


@dataclass(frozen=True)
class EntityIndex:
    by_symbol: dict[str, EntityRecord]
    aliases: dict[str, EntityRecord]

    @classmethod
    def empty(cls) -> "EntityIndex":
        return cls(by_symbol={}, aliases={})


def load_entity_index(paths: Iterable[str | Path] | None = None) -> EntityIndex:
    """Load a symbol/company index from existing repo universe CSVs when available."""
    candidates = list(paths or default_universe_paths())
    records: dict[str, EntityRecord] = {}
    for path_like in candidates:
        path = Path(path_like)
        if not path.exists() or not path.is_file():
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
                if not symbol or not re.fullmatch(r"[A-Z]{1,6}", symbol):
                    continue
                name = str(row.get("name") or row.get("company_name") or row.get("security_name") or "").strip()
                records[symbol] = EntityRecord(symbol=symbol, company_name=name)
    aliases: dict[str, EntityRecord] = {}
    for record in records.values():
        for alias in company_aliases(record.company_name):
            # Prefer less ambiguous, longer aliases.
            if len(alias) >= 4 and alias not in aliases:
                aliases[alias] = record
    return EntityIndex(by_symbol=records, aliases=aliases)


def default_universe_paths(root: str | Path = ".") -> list[Path]:
    root = Path(root)
    return [
        root / "data" / "universes" / "professional_universe.csv",
        root / "data" / "universes" / "core_universe.csv",
        root / "data" / "universe" / "professional_universe.csv",
        root / "data" / "universe.csv",
    ]


def company_aliases(name: str) -> set[str]:
    name = clean_company_name(name)
    if not name:
        return set()
    aliases = {name}
    no_suffix = clean_company_name(COMPANY_SUFFIX_RE.sub("", name))
    if no_suffix:
        aliases.add(no_suffix)
    # Drop exchange/class suffix fragments after separators.
    for sep in [" - ", " / ", ":"]:
        if sep in no_suffix:
            aliases.add(clean_company_name(no_suffix.split(sep, 1)[0]))
    return {a for a in aliases if is_usable_company_alias(a)}


def is_usable_company_alias(alias: str) -> bool:
    if len(alias) < 4 or alias.lower().startswith("the "):
        return False
    tokens = alias.split()
    if len(tokens) == 1 and alias.lower() in GENERIC_COMPANY_ALIASES:
        return False
    return True


def clean_company_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name or "")
    name = name.replace("&", " and ")
    name = SPACE_RE.sub(" ", name).strip(" .,;:-")
    return name


def extract_mentions_from_document(document: MarketContextDocument, index: EntityIndex | None = None) -> list[MarketContextMention]:
    index = index or EntityIndex.empty()
    text = "\n".join(part for part in [document.title, document.text] if part)
    if len(text) > MAX_ENTITY_TEXT_CHARS:
        text = text[:MAX_ENTITY_TEXT_CHARS]
    text_lower = text.lower()
    mentions: list[MarketContextMention] = []
    seen: set[tuple[str, int, int]] = set()

    for match in CASHTAG_RE.finditer(text):
        symbol = match.group(1).upper()
        if not re.fullmatch(r"[A-Z]{1,6}", symbol):
            continue
        mentions.append(_mention(document, symbol, match.start(), match.end(), text, index, confidence=0.95, kind="cashtag"))
        seen.add((symbol, match.start(), match.end()))

    for match in BARE_TICKER_RE.finditer(text):
        symbol = match.group(1).upper()
        if (symbol, match.start(), match.end()) in seen:
            continue
        if not is_allowed_bare_symbol(symbol, index):
            continue
        mentions.append(_mention(document, symbol, match.start(), match.end(), text, index, confidence=0.72, kind="bare_symbol"))
        seen.add((symbol, match.start(), match.end()))

    for alias, record in sorted(index.aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias.lower() not in text_lower:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)
        for match in pattern.finditer(text):
            symbol = record.symbol
            key = (symbol, match.start(), match.end())
            if key in seen:
                continue
            mentions.append(_mention(document, symbol, match.start(), match.end(), text, index, confidence=0.82, kind="company_name"))
            seen.add(key)

    deduped: dict[str, MarketContextMention] = {}
    for mention in mentions:
        deduped[mention.mention_id] = mention
    return list(deduped.values())


def annotate_document_entities(document: MarketContextDocument, mentions: list[MarketContextMention]) -> MarketContextDocument:
    symbols = sorted({mention.symbol for mention in mentions})
    companies = sorted({mention.company_name for mention in mentions if mention.company_name})
    return document.model_copy(update={"symbols_mentioned": symbols, "companies_mentioned": companies})


def is_allowed_bare_symbol(symbol: str, index: EntityIndex) -> bool:
    if symbol not in index.by_symbol:
        return False
    if symbol in COMMON_FALSE_POSITIVE_TICKERS:
        return False
    if len(symbol) == 1:
        return False
    return True


def _mention(
    document: MarketContextDocument,
    symbol: str,
    start: int,
    end: int,
    full_text: str,
    index: EntityIndex,
    *,
    confidence: float,
    kind: str,
) -> MarketContextMention:
    context = context_window(full_text, start, end)
    record = index.by_symbol.get(symbol)
    return MarketContextMention(
        mention_id=stable_mention_id(document.document_id, symbol, context),
        document_id=document.document_id,
        symbol=symbol,
        company_name=record.company_name if record else None,
        confidence=confidence,
        context_window=context,
        sentiment_hint=sentiment_hint(context),
        available_at=document.available_at,
        metadata_json={"match_type": kind, "match_start": start, "match_end": end},
    )


def context_window(text: str, start: int, end: int, *, chars: int = 180) -> str:
    left = max(0, start - chars)
    right = min(len(text), end + chars)
    context = text[left:right]
    return SPACE_RE.sub(" ", context).strip()


def sentiment_hint(context: str) -> str | None:
    words = {w.strip(".,!?;:()[]{}\"'").lower() for w in context.split()}
    bullish = bool(words & BULLISH_WORDS)
    bearish = bool(words & BEARISH_WORDS)
    if bullish and not bearish:
        return "bullish_keyword"
    if bearish and not bullish:
        return "bearish_keyword"
    if bullish and bearish:
        return "mixed_keywords"
    return None
