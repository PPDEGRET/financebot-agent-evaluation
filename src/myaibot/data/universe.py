"""Ticker universe and tradability hygiene.

Free/current sources are not fully survivorship-safe. This layer makes the risk
visible and keeps a swappable interface for paid point-in-time data later.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SP500_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Long-only equity/ETF/ETP proxies for macro/commodity themes. No futures,
# options, margin, inverse, or leveraged products. Some commodity funds are
# grantor trusts/commodity pools rather than operating companies; the variant
# briefs should still treat them as long-only exchange-traded products.
LEVERAGED_INVERSE_NAME_RE = re.compile(
    r"(?:ultrapro|ultrashort|inverse|\b\d+(?:\.\d+)?x\b|\bbull\s+\d+x\b|\bbear\s+\d+x\b|\bdaily\b.*\b(?:bull|bear|short)\b|\bshort\b.*\bdaily\b)",
    re.IGNORECASE,
)
NON_COMMON_PRODUCT_NAME_RE = re.compile(
    r"(?:warrant|rights?\b|\bunit\b|preferred|depositary share|exchange traded note|\betn\b|note due|debenture)",
    re.IGNORECASE,
)

PROFESSIONAL_THEME_PROXIES: tuple[dict[str, str], ...] = (
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "theme": "us_large_cap"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "theme": "nasdaq_100"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "theme": "us_small_cap"},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF", "theme": "us_total_market"},
    {"symbol": "DIA", "name": "SPDR Dow Jones Industrial Average ETF", "theme": "us_blue_chip"},
    {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "theme": "semiconductors"},
    {"symbol": "SOXX", "name": "iShares Semiconductor ETF", "theme": "semiconductors"},
    {"symbol": "XLK", "name": "Technology Select Sector SPDR", "theme": "sector_technology"},
    {"symbol": "XLF", "name": "Financial Select Sector SPDR", "theme": "sector_financials"},
    {"symbol": "XLE", "name": "Energy Select Sector SPDR", "theme": "sector_energy"},
    {"symbol": "XLV", "name": "Health Care Select Sector SPDR", "theme": "sector_healthcare"},
    {"symbol": "XLI", "name": "Industrial Select Sector SPDR", "theme": "sector_industrials"},
    {"symbol": "XLY", "name": "Consumer Discretionary Select Sector SPDR", "theme": "sector_discretionary"},
    {"symbol": "XLP", "name": "Consumer Staples Select Sector SPDR", "theme": "sector_staples"},
    {"symbol": "XLB", "name": "Materials Select Sector SPDR", "theme": "sector_materials"},
    {"symbol": "XLU", "name": "Utilities Select Sector SPDR", "theme": "sector_utilities"},
    {"symbol": "XLC", "name": "Communication Services Select Sector SPDR", "theme": "sector_communications"},
    {"symbol": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "theme": "rates_duration"},
    {"symbol": "IEF", "name": "iShares 7-10 Year Treasury Bond ETF", "theme": "rates_intermediate"},
    {"symbol": "SHY", "name": "iShares 1-3 Year Treasury Bond ETF", "theme": "rates_short"},
    {"symbol": "LQD", "name": "iShares Investment Grade Corporate Bond ETF", "theme": "credit_ig"},
    {"symbol": "HYG", "name": "iShares High Yield Corporate Bond ETF", "theme": "credit_hy"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "theme": "gold"},
    {"symbol": "IAU", "name": "iShares Gold Trust", "theme": "gold"},
    {"symbol": "GDX", "name": "VanEck Gold Miners ETF", "theme": "gold_miners"},
    {"symbol": "GDXJ", "name": "VanEck Junior Gold Miners ETF", "theme": "gold_miners"},
    {"symbol": "SLV", "name": "iShares Silver Trust", "theme": "silver"},
    {"symbol": "SIL", "name": "Global X Silver Miners ETF", "theme": "silver_miners"},
    {"symbol": "USO", "name": "United States Oil Fund", "theme": "oil"},
    {"symbol": "BNO", "name": "United States Brent Oil Fund", "theme": "oil"},
    {"symbol": "XOP", "name": "SPDR S&P Oil & Gas Exploration & Production ETF", "theme": "oil_equities"},
    {"symbol": "OIH", "name": "VanEck Oil Services ETF", "theme": "oil_services"},
    {"symbol": "UNG", "name": "United States Natural Gas Fund", "theme": "natural_gas"},
    {"symbol": "URA", "name": "Global X Uranium ETF", "theme": "uranium"},
    {"symbol": "URNM", "name": "Sprott Uranium Miners ETF", "theme": "uranium"},
    {"symbol": "CCJ", "name": "Cameco Corp", "theme": "uranium_equity"},
    {"symbol": "SRUUF", "name": "Sprott Physical Uranium Trust", "theme": "uranium_trust"},
    {"symbol": "COPX", "name": "Global X Copper Miners ETF", "theme": "copper_miners"},
    {"symbol": "CPER", "name": "United States Copper Index Fund", "theme": "copper"},
    {"symbol": "PICK", "name": "iShares MSCI Global Metals & Mining Producers ETF", "theme": "metals_miners"},
    {"symbol": "XME", "name": "SPDR S&P Metals & Mining ETF", "theme": "metals_miners"},
    {"symbol": "DBB", "name": "Invesco DB Base Metals Fund", "theme": "base_metals"},
    {"symbol": "AA", "name": "Alcoa Corp", "theme": "aluminum_equity"},
    {"symbol": "CENX", "name": "Century Aluminum", "theme": "aluminum_equity"},
    {"symbol": "REMX", "name": "VanEck Rare Earth/Strategic Metals ETF", "theme": "rare_earths"},
    {"symbol": "LIT", "name": "Global X Lithium & Battery Tech ETF", "theme": "lithium"},
    {"symbol": "WOOD", "name": "iShares Global Timber & Forestry ETF", "theme": "timber"},
    {"symbol": "DBA", "name": "Invesco DB Agriculture Fund", "theme": "agriculture"},
)


@dataclass(frozen=True)
class UniverseConfig:
    include_etfs: bool = True
    include_test_issues: bool = False
    min_symbol_length: int = 1
    max_symbol_length: int = 5
    exclude_leveraged_inverse: bool = True
    exclude_non_common_products: bool = True


def read_nasdaq_trader_files(directory: str | Path, *, include_other_listed: bool = True) -> pd.DataFrame:
    directory = Path(directory)
    frames: list[pd.DataFrame] = []
    nasdaq = directory / "nasdaqlisted.txt"
    other = directory / "otherlisted.txt"
    if nasdaq.exists():
        df = pd.read_csv(nasdaq, sep="|")
        df = df[df.columns.intersection(["Symbol", "Security Name", "Market Category", "Test Issue", "ETF", "Financial Status", "Round Lot Size"])]
        df = df.rename(columns={"Symbol": "symbol", "Security Name": "name", "Test Issue": "test_issue", "ETF": "etf"})
        df["source_nasdaq_listed"] = True
        frames.append(df)
    if include_other_listed and other.exists():
        df = pd.read_csv(other, sep="|")
        df = df[df.columns.intersection(["ACT Symbol", "Security Name", "Exchange", "Test Issue", "ETF", "Round Lot Size"])]
        df = df.rename(columns={"ACT Symbol": "symbol", "Security Name": "name", "Test Issue": "test_issue", "ETF": "etf"})
        df["source_other_listed"] = True
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["symbol", "name", "test_issue", "etf"])
    out = pd.concat(frames, ignore_index=True)
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out = out[~out["symbol"].str.contains("File Creation Time", na=False)]
    return _coalesce_universe_rows(out)


def download_nasdaq_trader_universe(out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for url, name in [(NASDAQ_LISTED_URL, "nasdaqlisted.txt"), (OTHER_LISTED_URL, "otherlisted.txt")]:
        request = Request(url, headers={"User-Agent": "FINANCEBOT research universe builder"})
        with urlopen(request, timeout=30) as response:  # nosec - official public URL configured above
            data = response.read()
        path = out / name
        path.write_bytes(data)
        paths.append(path)
    return paths[0], paths[1]


def load_sec_company_tickers(path_or_url: str | Path = SEC_COMPANY_TICKERS_URL) -> pd.DataFrame:
    if str(path_or_url).startswith("http"):
        request = Request(str(path_or_url), headers={"User-Agent": "FINANCEBOT research henri@example.invalid"})
        with urlopen(request, timeout=30) as response:  # nosec - SEC public URL
            raw = response.read().decode("utf-8")
        data = pd.read_json(io.StringIO(raw), orient="index")
    else:
        data = pd.read_json(Path(path_or_url), orient="index")
    data = data.rename(columns={"ticker": "symbol", "title": "company_name", "cik_str": "cik"})
    data["symbol"] = data["symbol"].astype(str).str.upper().str.strip()
    return data[["symbol", "cik", "company_name"]].drop_duplicates("symbol")


def load_sp500_wikipedia(path_or_url: str | Path = SP500_WIKIPEDIA_URL) -> pd.DataFrame:
    """Load the current S&P 500 member list from Wikipedia or a saved HTML file.

    This is not point-in-time for historical simulations; callers should record
    that survivorship/current-membership bias exists unless they use paid PIT data.
    """
    if str(path_or_url).startswith("http"):
        request = Request(str(path_or_url), headers={"User-Agent": "FINANCEBOT research universe builder"})
        with urlopen(request, timeout=30) as response:  # nosec - public page
            html = response.read().decode("utf-8", errors="replace")
    else:
        html = Path(path_or_url).read_text(encoding="utf-8", errors="replace")
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - optional dependency in data extra
        raise RuntimeError("Install beautifulsoup4/bs4 or provide a prebuilt S&P 500 CSV.") from exc
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise RuntimeError("Could not find S&P 500 constituents table in Wikipedia HTML.")
    rows: list[dict[str, str]] = []
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=False))
        symbol = row.get("Symbol", "").replace(".", "-").upper().strip()
        if symbol:
            rows.append(
                {
                    "symbol": symbol,
                    "name": row.get("Security", ""),
                    "sector": row.get("GICS Sector", ""),
                    "sub_industry": row.get("GICS Sub-Industry", ""),
                    "source_sp500": True,
                }
            )
    return pd.DataFrame(rows).drop_duplicates("symbol")


def professional_theme_universe() -> pd.DataFrame:
    df = pd.DataFrame(PROFESSIONAL_THEME_PROXIES)
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["source_theme_proxy"] = True
    return df.drop_duplicates("symbol")


def build_professional_universe(
    *,
    nasdaq_dir: str | Path | None = None,
    include_nasdaq_trader: bool = True,
    include_other_listed: bool = False,
    include_sp500: bool = True,
    include_theme_proxies: bool = True,
    config: UniverseConfig | None = None,
) -> pd.DataFrame:
    """Build an auditable broad universe for long-only research.

    The returned frame is current-membership/current-listing data, not
    point-in-time. It should be used for selection/tournament research only with
    that bias label unless replaced by a PIT source.
    """
    frames: list[pd.DataFrame] = []
    if include_nasdaq_trader:
        if nasdaq_dir is None:
            raise ValueError("nasdaq_dir is required when include_nasdaq_trader=True")
        frames.append(read_nasdaq_trader_files(nasdaq_dir, include_other_listed=include_other_listed))
    if include_sp500:
        frames.append(load_sp500_wikipedia())
    if include_theme_proxies:
        frames.append(professional_theme_universe())
    if not frames:
        return pd.DataFrame(columns=["symbol"])
    universe = _coalesce_universe_rows(pd.concat(frames, ignore_index=True, sort=False))
    universe = filter_tradable_universe(universe, config or UniverseConfig(include_etfs=True))
    bool_cols = [c for c in universe.columns if c.startswith("source_")]
    for col in bool_cols:
        universe[col] = universe[col].fillna(False).astype(bool)
    source_cols = sorted(bool_cols)
    other_cols = [c for c in universe.columns if c not in {"symbol", *source_cols}]
    return universe[["symbol", *source_cols, *other_cols]].sort_values("symbol").reset_index(drop=True)


def filter_tradable_universe(frame: pd.DataFrame, config: UniverseConfig | None = None) -> pd.DataFrame:
    cfg = config or UniverseConfig()
    if frame.empty:
        return frame.copy()
    df = frame.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df = df[df["symbol"].str.len().between(cfg.min_symbol_length, cfg.max_symbol_length)]
    df = df[~df["symbol"].str.contains(r"[.$^/ ]", regex=True, na=False)]
    if not cfg.include_test_issues and "test_issue" in df.columns:
        df = df[~df["test_issue"].astype(str).str.upper().eq("Y")]
    if not cfg.include_etfs and "etf" in df.columns:
        df = df[~df["etf"].astype(str).str.upper().eq("Y")]
    name = df["name"].fillna("").astype(str) if "name" in df.columns else pd.Series("", index=df.index)
    if cfg.exclude_leveraged_inverse:
        df = df[~name.str.contains(LEVERAGED_INVERSE_NAME_RE, regex=True, na=False)]
        name = df["name"].fillna("").astype(str) if "name" in df.columns else pd.Series("", index=df.index)
    if cfg.exclude_non_common_products:
        # Keep explicitly curated theme proxies even if names contain words like "Fund".
        theme_proxy = df.get("source_theme_proxy", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        df = df[theme_proxy | ~name.str.contains(NON_COMMON_PRODUCT_NAME_RE, regex=True, na=False)]
    return df.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def _coalesce_universe_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    df = frame.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    bool_cols = [c for c in df.columns if c.startswith("source_")]
    for col in bool_cols:
        df[col] = df[col].fillna(False).astype(bool)
    text_cols = [c for c in df.columns if c not in {"symbol", *bool_cols}]
    rows: list[dict[str, object]] = []
    for symbol, group in df.groupby("symbol", sort=True):
        row: dict[str, object] = {"symbol": symbol}
        for col in bool_cols:
            row[col] = bool(group[col].fillna(False).astype(bool).any())
        for col in text_cols:
            vals = [str(v).strip() for v in group[col].dropna().tolist() if str(v).strip() and str(v).strip().lower() != "nan"]
            if vals:
                row[col] = vals[0]
        rows.append(row)
    return pd.DataFrame(rows)
