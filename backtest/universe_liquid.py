"""A curated liquid large/mid-cap universe for backtesting.

These are the kind of names the live scanner targets (price > $5, deep
liquidity, real ATR). Using a fixed list keeps the first backtest reproducible
and avoids survivorship/data-quality noise from the full Nasdaq directory.
Scale up to the full S&P 500 once the engine is validated.
"""

LIQUID_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "AMD", "NFLX",
    # Semis / hardware
    "MU", "QCOM", "INTC", "TXN", "AMAT", "LRCX", "MRVL", "ON", "SMCI", "ARM",
    # Software / internet
    "CRM", "ADBE", "ORCL", "NOW", "SHOP", "SNOW", "PLTR", "UBER", "ABNB", "PANW",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "AXP", "COF", "V",
    # Consumer / retail
    "COST", "WMT", "HD", "NKE", "SBUX", "MCD", "TGT", "LULU", "CMG", "DIS",
    # Health / pharma
    "UNH", "LLY", "JNJ", "MRK", "PFE", "ABBV", "TMO", "ISRG", "VRTX", "GILD",
    # Energy / industrial
    "XOM", "CVX", "COP", "SLB", "CAT", "DE", "BA", "GE", "HON", "UPS",
    # High-beta / momentum favorites
    "COIN", "MARA", "RIVN", "DKNG", "ROKU", "SQ", "PYPL", "CVNA", "AFRM", "DDOG",
]
