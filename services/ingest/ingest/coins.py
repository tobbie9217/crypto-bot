"""
Single source of truth for tracked coins.

`detect_coin` returns the first matching ticker for a given text, using
word-boundary regex to avoid false positives like "SOL" matching "sold".

`TRACKED` carries metadata each collector needs (CoinGecko id for the
market collector, etc.). Order matters: the first matching ticker wins.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CoinSpec:
    symbol: str           # canonical ticker stored in posts.coin
    coingecko_id: str     # for /coins/markets and DefiLlama lookups
    keywords: tuple[str, ...]   # case-insensitive whole-word matches
    binance_perp: str | None = None  # USD-M perp symbol on Binance Futures, or None


# Stablecoins (USDT, USDC, DAI) intentionally omitted — they don't move
# on sentiment and would dominate matches in any market commentary.
TRACKED: tuple[CoinSpec, ...] = (
    CoinSpec("BTC",   "bitcoin",            ("btc", "bitcoin", "xbt"),     "BTCUSDT"),
    CoinSpec("ETH",   "ethereum",           ("eth", "ethereum", "ether"),  "ETHUSDT"),
    CoinSpec("BNB",   "binancecoin",        ("bnb",),                      "BNBUSDT"),
    CoinSpec("SOL",   "solana",             ("sol", "solana"),             "SOLUSDT"),
    CoinSpec("XRP",   "ripple",             ("xrp", "ripple"),             "XRPUSDT"),
    CoinSpec("DOGE",  "dogecoin",           ("doge", "dogecoin"),          "DOGEUSDT"),
    CoinSpec("ADA",   "cardano",            ("ada", "cardano"),            "ADAUSDT"),
    CoinSpec("AVAX",  "avalanche-2",        ("avax", "avalanche"),         "AVAXUSDT"),
    # SHIB and PEPE trade as 1000x lots on Binance perps.
    CoinSpec("SHIB",  "shiba-inu",          ("shib", "shiba"),             "1000SHIBUSDT"),
    CoinSpec("TRX",   "tron",               ("trx", "tron"),               "TRXUSDT"),
    CoinSpec("DOT",   "polkadot",           ("polkadot",),                 "DOTUSDT"),
    CoinSpec("LINK",  "chainlink",          ("chainlink",),                "LINKUSDT"),
    CoinSpec("MATIC", "matic-network",      ("matic", "polygon"),          "POLUSDT"),
    CoinSpec("UNI",   "uniswap",            ("uniswap",),                  "UNIUSDT"),
    CoinSpec("LTC",   "litecoin",           ("ltc", "litecoin"),           "LTCUSDT"),
    CoinSpec("NEAR",  "near",               ("near protocol",),            "NEARUSDT"),
    CoinSpec("ATOM",  "cosmos",             ("atom", "cosmos"),            "ATOMUSDT"),
    CoinSpec("ICP",   "internet-computer",  ("icp",),                      "ICPUSDT"),
    CoinSpec("APT",   "aptos",              ("aptos",),                    "APTUSDT"),
    CoinSpec("ARB",   "arbitrum",           ("arbitrum",),                 "ARBUSDT"),
    CoinSpec("OP",    "optimism",           ("optimism",),                 "OPUSDT"),
    CoinSpec("INJ",   "injective-protocol", ("inj", "injective"),          "INJUSDT"),
    CoinSpec("SEI",   "sei-network",        ("sei",),                      "SEIUSDT"),
    CoinSpec("SUI",   "sui",                ("sui",),                      "SUIUSDT"),
    CoinSpec("PEPE",  "pepe",               ("pepe",),                     "1000PEPEUSDT"),
    CoinSpec("WLD",   "worldcoin-wld",      ("wld", "worldcoin"),          "WLDUSDT"),
    CoinSpec("RUNE",  "thorchain",          ("rune", "thorchain"),         "RUNEUSDT"),
    CoinSpec("FIL",   "filecoin",           ("filecoin",),                 "FILUSDT"),
    CoinSpec("RNDR",  "render-token",       ("rndr", "render token"),      "RENDERUSDT"),
    CoinSpec("TIA",   "celestia",           ("tia", "celestia"),           "TIAUSDT"),
    CoinSpec("AAVE",  "aave",               ("aave",),                     "AAVEUSDT"),
    CoinSpec("MNT",   "mantle",             ("mnt",),                      "MNTUSDT"),
)

COIN_BY_SYMBOL: dict[str, CoinSpec] = {c.symbol: c for c in TRACKED}


def _build_pattern(spec: CoinSpec) -> re.Pattern:
    # Whole-word match for any keyword, OR explicit cashtag like $btc.
    words = "|".join(re.escape(k) for k in spec.keywords)
    return re.compile(
        rf"(?:\b(?:{words})\b)|(?:\${re.escape(spec.symbol)}\b)",
        re.IGNORECASE,
    )


_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (spec.symbol, _build_pattern(spec)) for spec in TRACKED
)


def detect_coin(text: str) -> str | None:
    """Return the first tracked ticker mentioned in `text`, or None."""
    for symbol, pattern in _PATTERNS:
        if pattern.search(text):
            return symbol
    return None
