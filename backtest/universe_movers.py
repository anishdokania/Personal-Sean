"""High-ADR "movers" universe for the Sean / Options Cartel style.

The example charts (TeraWulf, Rocket Lab, D-Wave, MSTR, ...) are fast, high-ADR
names, not mega-caps. This is a curated pool of the kind of momentum names that
style trades; the point-in-time ADR% gate in signals.py then selects only the
names that were actually moving fast enough on a given date.

This is a pragmatic first pass (not survivorship-free) -- the fuller version
screens the whole Nasdaq-listed universe (universe.py) by ADR%. Kept as a fixed
list for now so the backtest is fast and reproducible.
"""

MOVERS_UNIVERSE = [
    # Crypto miners / crypto-levered
    "MARA", "RIOT", "WULF", "CLSK", "CIFR", "BITF", "HUT", "IREN", "BTBT",
    "COIN", "MSTR", "HOOD", "BMNR", "CORZ",
    # Quantum
    "QBTS", "IONQ", "RGTI", "QUBT",
    # Nuclear / uranium / power
    "OKLO", "SMR", "NNE", "LEU", "UEC", "CCJ", "VST", "TLN", "GEV", "NRG",
    # Space / defense tech
    "RKLB", "ASTS", "LUNR", "ACHR", "JOBY", "KTOS", "RDW",
    # AI / data / semis momentum
    "SMCI", "NVDA", "AMD", "ARM", "MRVL", "AVGO", "PLTR", "MU", "CRDO",
    "ALAB", "NBIS", "AI", "BBAI", "SOUN", "TEM", "VRT", "POWL", "APLD",
    # High-beta growth / software
    "APP", "HIMS", "AFRM", "UPST", "SOFI", "DKNG", "RBLX", "SNAP", "CVNA",
    "SHOP", "NET", "DDOG", "CRWD", "SNOW", "PATH", "TTD", "RDDT", "DASH",
    # EV / clean / battery
    "TSLA", "RIVN", "LCID", "CHPT", "ENVX", "QS", "PLUG", "FCEL", "RUN", "FSLR",
    # Biotech / pharma movers
    "VKTX", "CRSP", "NTLA", "RXRX", "SAVA", "MRNA", "TGTX", "SMMT", "IONS",
    # China / high-vol ADRs
    "PDD", "BABA", "NIO", "XPEV", "LI", "BIDU", "FUTU", "TCOM",
    # Misc momentum / retail favorites
    "GME", "AMC", "CVNA", "CELH", "ELF", "DUOL", "ONON", "CART", "TOST",
    "IBIT", "MSTX",
]

# De-duplicate while preserving order.
_seen = set()
MOVERS_UNIVERSE = [s for s in MOVERS_UNIVERSE if not (s in _seen or _seen.add(s))]
