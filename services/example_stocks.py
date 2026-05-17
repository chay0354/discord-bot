from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.finnhub_client import format_quote, get_company_profile, get_quote

# How many example stocks to show per click in pre-voting channels.
EXAMPLES_PAGE_SIZE = 20

EXAMPLE_STOCKS: dict[str, list[str]] = {
    "small": [
        "PRTS", "BLNK", "RZLV", "SSTI", "SPCB", "KULR", "GPRO", "TUP", "BB", "VUZI",
        "MVIS", "DDD", "VLD", "NNDM", "WULF", "HUT", "BTBT", "BITF", "SDIG", "CAN",
        "HIVE", "CIFR", "IREN", "GREE", "AUR", "OUST", "AEVA", "LIDR", "INVZ", "LAZR",
        "HYLN", "WKHS", "GOEV", "CENN", "SOLO", "MULN", "FSR", "NKLA", "RIDE", "FFIE",
        "PLUG", "FCEL", "BE", "BLDP", "CLNE", "GEVO", "AMTX", "REI", "BORR", "VTLE",
        "TELL", "CEI", "NINE", "KLXE", "RIG", "VAL", "NEXT", "LUMN", "OPK", "AGEN",
        "CYDY", "SAVA", "ATOS", "OCGN", "BNGO", "EDIT", "CRSP", "BEAM", "NTLA", "FATE",
        "PACB", "DNA", "CDXS", "ZYME", "VIR", "NVAX", "INO", "VBIV", "ALT", "ABUS",
        "MNKD", "OCUL", "EYPT", "KPTI", "KURA", "GERN", "AXSM", "ARDX", "SENS", "PAVM",
        "RRGB", "BBBY", "BIG", "KOSS", "GME", "AMC", "EXPR", "LEDS", "MARK", "IZEA",
    ],
    "mid": [
        "CROX", "ETSY", "FIVE", "WING", "SHAK", "CHWY", "BROS", "OLLI", "WEN", "DIN",
        "TXRH", "CAKE", "PLAY", "DRI", "YETI", "COLM", "DECK", "SKX", "BOOT", "SIG",
        "URBN", "ANF", "AEO", "GPS", "VSCO", "LEVI", "BKE", "JWN", "M", "KSS",
        "BURL", "ROST", "TJX", "W", "OSTK", "QRTEA", "REAL", "RVLV", "FTCH", "GLBE",
        "NVEI", "FOUR", "BILL", "MQ", "AFRM", "UPST", "SOFI", "NU", "HOOD", "COIN",
        "RBLX", "TTD", "APP", "U", "FVRR", "DLO", "ZI", "DOCN", "PATH", "PD",
        "ESTC", "MDB", "DDOG", "NET", "S", "OKTA", "TWLO", "BOX", "DBX", "SMAR",
        "GTLB", "ALTR", "VRNS", "QLYS", "TENB", "RPD", "GEN", "CYBR", "MNDY", "ASAN",
        "RUN", "NOVA", "SEDG", "ENPH", "FSLR", "ARRY", "SPWR", "FLNC", "STEM", "QS",
        "MP", "ALB", "LTHM", "LAC", "PLL", "AA", "CLF", "X", "NUE", "STLD",
    ],
    "blue": [
        "AAPL", "MSFT", "NVDA", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "BRK",
        "JPM", "V", "LLY", "UNH", "MA", "XOM", "COST", "WMT", "PG", "HD",
        "ORCL", "JNJ", "BAC", "ABBV", "KO", "NFLX", "CRM", "MRK", "CVX", "PEP",
        "ADBE", "AMD", "TMO", "LIN", "MCD", "CSCO", "ABT", "ACN", "WFC", "QCOM",
        "TXN", "IBM", "GE", "DHR", "VZ", "AMGN", "INTU", "PFE", "NOW", "ISRG",
        "AMAT", "CAT", "DIS", "NEE", "UBER", "RTX", "SPGI", "PM", "LOW", "UNP",
        "HON", "PGR", "BKNG", "ETN", "T", "COP", "ELV", "TJX", "VRTX", "SYK",
        "LMT", "REGN", "C", "BLK", "PLD", "ADP", "MDT", "PANW", "BA", "ADI",
        "CB", "MMC", "SBUX", "GILD", "DE", "AMT", "MU", "SO", "BSX", "KLAC",
        "FI", "LRCX", "MO", "ICE", "EQIX", "DUK", "SHW", "ZTS", "APH", "CME",
    ],
}


def example_stock_lines_for_symbols(symbols: list[str]) -> list[str]:
    if not symbols:
        return []
    with ThreadPoolExecutor(max_workers=12) as executor:
        quote_results = list(executor.map(get_quote, symbols))
        profile_results = list(executor.map(get_company_profile, symbols))
    quotes = {quote.symbol: quote for quote in quote_results if quote}
    names = {
        symbol: str(profile.get("name") or "").strip()
        for symbol, profile in zip(symbols, profile_results)
        if profile
    }
    lines: list[str] = []
    for symbol in symbols:
        name = names.get(symbol) or "Company name unavailable"
        lines.append(f"{format_quote(symbol, quotes.get(symbol))} - {name}")
    return lines


def example_stock_lines_slice(category: str, offset: int, limit: int) -> list[str]:
    """Fetch formatted lines for a slice of the static example list (offset/limit in symbols)."""
    all_syms = EXAMPLE_STOCKS.get(category, [])
    symbols = all_syms[offset : offset + limit]
    return example_stock_lines_for_symbols(symbols)


def example_stock_lines(category: str) -> list[str]:
    """First 40 example symbols with live quote + name (legacy helper)."""
    symbols = EXAMPLE_STOCKS.get(category, [])[:40]
    return example_stock_lines_for_symbols(symbols)
