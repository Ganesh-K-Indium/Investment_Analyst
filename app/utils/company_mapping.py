"""
Mapping of stock tickers to company names and utility functions.
"""

import re

TICKER_TO_COMPANY = {
    # Tech companies
    'aapl': 'apple',
    'msft': 'microsoft',
    'googl': 'google',
    'amzn': 'amazon',
    'nvda': 'nvidia',
    'tsla': 'tesla',
    'meta': 'meta',
    'nflx': 'netflix',
    'amd': 'amd',
    'intc': 'intel',
    'qcom': 'qualcomm',
    'csco': 'cisco',
    'acn': 'accenture',
    'ibm': 'ibm',
    'orcl': 'oracle',
    'sap': 'sap',
    'crm': 'salesforce',
    'adbe': 'adobe',
    'uber': 'uber',
    'lyft': 'lyft',
    'shop': 'shopify',
    'spot': 'spotify',
    'zm': 'zoom',
    'twlo': 'twilio',
    'coin': 'coinbase',
    'pypl': 'paypal',
    'sqm': 'square',
    'snps': 'synopsys',
    'cdns': 'cadence',
    'tsm': 'taiwan semiconductor manufacturing',
    'asml': 'asml',
    'arm': 'arm',
    'avgo': 'broadcom',

    # Financial companies
    'jpm': 'jpmorgan chase',
    'bac': 'bank of america',
    'gs': 'goldman sachs',
    'ms': 'morgan stanley',
    'wu': 'western union',
    'v': 'visa',
    'ma': 'mastercard',
    'axa': 'axa',
    'axp': 'american express',
    'c': 'citigroup',
    'wfc': 'wells fargo',
    'tfc': 'truist financial',
    'bnc': 'banco',
    
    # Healthcare
    'jnj': 'johnson & johnson',
    'lly': 'eli lilly',
    'pfe': 'pfizer',
    'mrna': 'moderna',
    'abbv': 'abbvie',
    'ulvr': 'unilever',
    'cvx': 'chevron',
    'cop': 'conocophillips',
    'slb': 'schlumberger',
    
    # Energy
    'psa': 'peugeot',
    'bp': 'bp',
    'rds': 'royal dutch shell',
    'shel': 'shell',
    'xom': 'exxon mobil',
    
    # Retail & Consumer
    'wmt': 'walmart',
    'hd': 'home depot',
    'ko': 'coca cola',
    'pep': 'pepsi',
    'mcd': 'mcdonalds',
    'nke': 'nike',
    'lulu': 'lululemon',
    'nvr': 'nvr',
    'hm': 'h&m group',
    'pm': 'philip morris',
    'mbb': 'mutual',
    
    # Automotive
    'gm': 'general motors',
    'f': 'ford',
    'gme': 'gamestop',
    'tsla': 'tesla',
    
    # Industrial
    'ba': 'boeing',
    'ge': 'general electric',
    'cat': 'caterpillar',
    'hog': 'harley davidson',
    
    # Real Estate
    'vno': 'vornado realty trust',
    'spg': 'simon property group',
    'pld': 'prologis',

    # Foreign private issuers (file 20-F / 6-K instead of 10-K / 10-Q / 8-K).
    # Tickers are the US-listed ADS/ordinary tickers EDGAR knows them by (e.g.
    # Embraer is EMBJ, not its old ERJ). tsm / sap / asml / shel / bp / spot above
    # are also foreign issuers — see FOREIGN_PRIVATE_ISSUER_TICKERS below.
    # Deliberately NOT added: very short/common-word names (nio, vale, ...) —
    # get_ticker() substring-matches names, so e.g. 'vale' would capture
    # "Valero Energy".
    'sny': 'sanofi',
    'embj': 'embraer',
    'tm': 'toyota',
    'infy': 'infosys',
    'baba': 'alibaba',
    'sony': 'sony',
    'hmc': 'honda',
    'wit': 'wipro',
    'hdb': 'hdfc bank',
    'ibn': 'icici bank',
    'nvo': 'novo nordisk',
    'nvs': 'novartis',
    'azn': 'astrazeneca',
    'gsk': 'gsk',
    'tte': 'totalenergies',
    'deo': 'diageo',
    'bti': 'british american tobacco',
    'rio': 'rio tinto',
    'bhp': 'bhp',
    'pbr': 'petrobras',
    'itub': 'itau unibanco',
    'pdd': 'pdd holdings',
    'bidu': 'baidu',
    'jd': 'jd.com',
}

# Reverse mapping for looking up ticker by company name
COMPANY_TO_TICKER = {v: k for k, v in TICKER_TO_COMPANY.items()}

# Manual aliases for companies commonly referred to by more than one name,
# where TICKER_TO_COMPANY only stores a single canonical name per ticker
# (e.g. GOOGL's legal/parent name "Alphabet" vs. the product name "Google").
# A prior duplicate 'googl' key here silently dropped the 'alphabet' mapping
# entirely (Python keeps only the last literal key), so "Alphabet" as a
# company name resolved to no ticker at all.
COMPANY_TO_TICKER['alphabet'] = 'googl'
COMPANY_TO_TICKER['glaxosmithkline'] = 'gsk'
COMPANY_TO_TICKER['taiwan semiconductor'] = 'tsm'
COMPANY_TO_TICKER['tsmc'] = 'tsm'
COMPANY_TO_TICKER['toyota motor'] = 'tm'
COMPANY_TO_TICKER['embraer s.a.'] = 'embj'
COMPANY_TO_TICKER['shell plc'] = 'shel'

def get_company_name(ticker: str) -> str:
    """Get company name from ticker symbol."""
    if not ticker:
        return ""
    return TICKER_TO_COMPANY.get(ticker.lower(), ticker.lower())


def get_company_aliases(ticker: str) -> list:
    """
    All known company-name aliases that resolve to this ticker (e.g.
    'googl' -> ['google', 'alphabet']). get_company_name() only returns the
    single canonical TICKER_TO_COMPANY name — callers matching free-text
    against a company name (e.g. detecting which company a sub-query
    mentions) need every alias, not just the canonical one, or a mention of
    "Alphabet" won't match ticker 'googl' even though get_ticker("Alphabet")
    resolves it correctly.
    """
    if not ticker:
        return []
    ticker_lower = ticker.lower().split('_')[0]
    return [name for name, t in COMPANY_TO_TICKER.items() if t == ticker_lower]

# Fiscal-year-end month (1-12), explicitly enumerated for the top ~100 US
# companies by market cap rather than left to silently fall through to the
# default — most of these ARE calendar-year filers (month=12), listed
# explicitly here so coverage of the mega/large-cap universe is a known,
# reviewable fact rather than an assumption. Tickers not in this table still
# fall back to DEFAULT_FISCAL_YEAR_END_MONTH (12), which remains correct for
# the long tail of smaller-cap calendar-year filers.
TICKER_TO_FISCAL_YEAR_END_MONTH = {
    # --- Mega-cap tech ---
    'aapl': 9,    # Apple — last Saturday of September
    'msft': 6,    # Microsoft — June 30
    'googl': 12,  # Alphabet
    'goog': 12,   # Alphabet (class C)
    'amzn': 12,   # Amazon
    'nvda': 1,    # Nvidia — last Sunday of January
    'meta': 12,   # Meta Platforms
    'tsla': 12,   # Tesla
    'avgo': 10,   # Broadcom — Sunday closest to October 31
    'orcl': 5,    # Oracle — May 31
    'crm': 1,     # Salesforce — January 31
    'adbe': 11,   # Adobe — Friday closest to November 30
    'csco': 7,    # Cisco — Saturday closest to July 31
    'acn': 8,     # Accenture — August 31
    'ibm': 12,    # IBM
    'intc': 12,   # Intel
    'qcom': 9,    # Qualcomm — last Sunday of September
    'txn': 12,    # Texas Instruments
    'amd': 12,    # AMD
    'now': 12,    # ServiceNow
    'intu': 7,    # Intuit — July 31
    'amat': 10,   # Applied Materials — Sunday closest to October 31
    'mu': 8,      # Micron — Thursday closest to August 31
    'panw': 7,    # Palo Alto Networks — July 31
    'adi': 10,    # Analog Devices — Saturday closest to October 31
    'lrcx': 6,    # Lam Research — last Sunday of June
    'klac': 6,    # KLA Corp — June 30
    'snps': 10,   # Synopsys — Saturday closest to October 31
    'cdns': 12,   # Cadence Design Systems
    'anet': 12,   # Arista Networks
    'mrvl': 1,    # Marvell Technology — Saturday closest to January 31
    'ftnt': 12,   # Fortinet
    'pypl': 12,   # PayPal
    'adsk': 1,    # Autodesk — last Friday of January
    'shop': 12,   # Shopify
    'arm': 3,     # Arm Holdings — March 31
    'asml': 12,   # ASML
    'tsm': 12,    # Taiwan Semiconductor Manufacturing

    # --- Consumer / retail ---
    'wmt': 1,     # Walmart — January 31
    'hd': 1,      # Home Depot — Sunday closest to January 31
    'low': 1,     # Lowe's — Friday closest to January 31
    'cost': 8,    # Costco — Sunday closest to August 31
    'tgt': 1,     # Target — Saturday closest to January 31
    'nke': 5,     # Nike — May 31
    'lulu': 1,    # Lululemon — last Sunday of January
    'sbux': 9,    # Starbucks — Sunday closest to September 30
    'mcd': 12,    # McDonald's
    'yum': 12,    # Yum! Brands
    'cmg': 12,    # Chipotle
    'bkng': 12,   # Booking Holdings
    'tjx': 1,     # TJX Companies — Saturday closest to January 31
    'ko': 12,     # Coca-Cola
    'pep': 12,    # PepsiCo (last Saturday in December)
    'pg': 6,      # Procter & Gamble — June 30
    'cl': 12,     # Colgate-Palmolive
    'kmb': 12,    # Kimberly-Clark
    'khc': 12,    # Kraft Heinz
    'mdlz': 12,   # Mondelez
    'gis': 5,     # General Mills — last Sunday of May
    'hsy': 12,    # Hershey
    'stz': 2,     # Constellation Brands — February 28/29
    'mo': 12,     # Altria
    'pm': 12,     # Philip Morris International
    'el': 6,      # Estee Lauder — June 30
    'kr': 1,      # Kroger — Saturday closest to January 31
    'dltr': 1,    # Dollar Tree — Saturday closest to January 31
    'dg': 1,      # Dollar General — Friday closest to January 31

    # --- Financials ---
    'jpm': 12,    # JPMorgan Chase
    'bac': 12,    # Bank of America
    'wfc': 12,    # Wells Fargo
    'gs': 12,     # Goldman Sachs
    'ms': 12,     # Morgan Stanley
    'c': 12,      # Citigroup
    'usb': 12,    # US Bancorp
    'pnc': 12,    # PNC Financial
    'schw': 12,   # Charles Schwab
    'blk': 12,    # BlackRock
    'spgi': 12,   # S&P Global
    'axp': 12,    # American Express
    'v': 9,       # Visa — September 30
    'ma': 12,     # Mastercard
    'tfc': 12,    # Truist Financial
    'cof': 12,    # Capital One

    # --- Healthcare ---
    'unh': 12,    # UnitedHealth Group
    'jnj': 12,    # Johnson & Johnson
    'lly': 12,    # Eli Lilly
    'pfe': 12,    # Pfizer
    'mrk': 12,    # Merck
    'abbv': 12,   # AbbVie
    'tmo': 12,    # Thermo Fisher Scientific
    'abt': 12,    # Abbott Laboratories
    'dhr': 12,    # Danaher
    'bmy': 12,    # Bristol Myers Squibb
    'amgn': 12,   # Amgen
    'gild': 12,   # Gilead Sciences
    'cvs': 12,    # CVS Health
    'mdt': 4,     # Medtronic — last Friday of April
    'isrg': 12,   # Intuitive Surgical
    'vrtx': 12,   # Vertex Pharmaceuticals
    'regn': 12,   # Regeneron
    'mrna': 12,   # Moderna

    # --- Energy ---
    'xom': 12,    # ExxonMobil
    'cvx': 12,    # Chevron
    'cop': 12,    # ConocoPhillips
    'slb': 12,    # Schlumberger
    'eog': 12,    # EOG Resources
    'mpc': 12,    # Marathon Petroleum
    'psx': 12,    # Phillips 66

    # --- Industrials ---
    'ba': 12,     # Boeing
    'cat': 12,    # Caterpillar
    'hon': 12,    # Honeywell
    'ups': 12,    # UPS
    'rtx': 12,    # RTX Corp
    'lmt': 12,    # Lockheed Martin
    'ge': 12,     # General Electric
    'mmm': 12,    # 3M
    'de': 10,     # Deere & Co — Sunday closest to October 31
    'unp': 12,    # Union Pacific
    'fdx': 5,     # FedEx — May 31
    'noc': 12,    # Northrop Grumman
    'gd': 12,     # General Dynamics
    'emr': 9,     # Emerson Electric — September 30
    'gm': 12,     # General Motors
    'f': 12,      # Ford

    # --- Communication / media ---
    'dis': 9,     # Disney — Saturday closest to September 30
    'cmcsa': 12,  # Comcast
    'nflx': 12,   # Netflix
    't': 12,      # AT&T
    'vz': 12,     # Verizon
    'tmus': 12,   # T-Mobile US
    'wbd': 12,    # Warner Bros. Discovery

    # --- Real estate / other ---
    'pld': 12,    # Prologis
    'amt': 12,    # American Tower

    # --- Foreign private issuers (20-F / 6-K filers) ---
    # Months below come from EDGAR's `fiscalYearEnd` (verified) except where
    # EDGAR leaves it blank (baba, gsk, bti, embj) — those are from each
    # company's own reporting calendar. Japanese and Indian issuers mostly
    # report on an April-March year, which is exactly the case where falling
    # back to 12 would make "most recent filed year" one year stale.
    'sap': 12,    # SAP (tsm / asml are listed with the tech names above)
    'shel': 12,   # Shell
    'bp': 12,     # BP
    'spot': 12,   # Spotify
    'sny': 12,    # Sanofi
    'embj': 12,   # Embraer (EDGAR blank; calendar year)
    'tm': 3,      # Toyota — March 31
    'sony': 3,    # Sony Group — March 31
    'hmc': 3,     # Honda — March 31
    'infy': 3,    # Infosys — March 31
    'wit': 3,     # Wipro — March 31
    'hdb': 3,     # HDFC Bank — March 31
    'ibn': 3,     # ICICI Bank — March 31
    'baba': 3,    # Alibaba — March 31 (EDGAR blank)
    'nvo': 12,    # Novo Nordisk
    'nvs': 12,    # Novartis
    'azn': 12,    # AstraZeneca
    'gsk': 12,    # GSK (EDGAR blank; calendar year)
    'tte': 12,    # TotalEnergies
    'deo': 6,     # Diageo — June 30
    'bti': 12,    # British American Tobacco (EDGAR blank; calendar year)
    'rio': 12,    # Rio Tinto
    'bhp': 6,     # BHP — June 30
    'pbr': 12,    # Petrobras
    'itub': 12,   # Itau Unibanco
    'pdd': 12,    # PDD Holdings
    'bidu': 12,   # Baidu
    'jd': 12,     # JD.com
}

DEFAULT_FISCAL_YEAR_END_MONTH = 12  # calendar year end — fallback for tickers not in the table above

# Tickers whose annual report is a 20-F (interim/event reports on 6-K) rather
# than a 10-K. A 20-F is due 4 months after fiscal year end (vs 60-90 days for a
# 10-K), so even a calendar-year foreign issuer has no filed annual report for
# the year just ended until ~April — see get_most_recent_filed_fiscal_year().
FOREIGN_PRIVATE_ISSUER_TICKERS = frozenset({
    'tsm', 'sap', 'asml', 'shel', 'bp', 'spot', 'sny', 'embj', 'tm', 'sony', 'hmc',
    'infy', 'wit', 'hdb', 'ibn', 'baba', 'nvo', 'nvs', 'azn', 'gsk', 'tte', 'deo',
    'bti', 'rio', 'bhp', 'pbr', 'itub', 'pdd', 'bidu', 'jd',
})


def is_foreign_private_issuer(ticker: str) -> bool:
    """True if the ticker files 20-F/6-K (foreign private issuer) rather than 10-K/10-Q/8-K."""
    return bool(ticker) and ticker.lower().split('_')[0] in FOREIGN_PRIVATE_ISSUER_TICKERS


# Home-market rate / inflation sources for foreign issuers. The U.S. FRED/BLS store
# says nothing about these, so the macro context for an FPI is fetched from its home
# market's central bank and statistics office. Tickers not listed fall back to an
# unrestricted web search (see get_fpi_home_market).
FPI_HOME_MARKETS = {
    'embj': {'market': 'Brazil', 'rate': 'Selic policy rate', 'inflation': 'IPCA inflation',
             'domains': ['bcb.gov.br', 'ibge.gov.br']},
    'sny':  {'market': 'euro area (France)', 'rate': 'ECB deposit facility rate', 'inflation': 'euro area HICP inflation',
             'domains': ['ecb.europa.eu', 'banque-france.fr', 'insee.fr']},
}


def get_fpi_home_market(ticker: str):
    """Home-market macro sources for a foreign private issuer, or None if the ticker is not one."""
    if not is_foreign_private_issuer(ticker):
        return None
    return FPI_HOME_MARKETS.get(ticker.lower().split('_')[0], {'market': None, 'domains': []})


def form4_exemption_message(ticker: str) -> str:
    """
    Why there is no Form 4 data for a foreign private issuer. Their officers,
    directors and 10% holders are exempt from Section 16 of the Exchange Act
    (Rule 3a12-3(b)), so no Form 4 is ever filed — "no data" here is a fact about
    the issuer, not a gap in ingestion.
    """
    return (
        f"{ticker.upper()} is a foreign private issuer. Its officers, directors and 10% holders "
        f"are exempt from Section 16 of the Exchange Act, so no Form 4 insider-transaction filings "
        f"exist for it. Share ownership of directors and senior management, major shareholders and "
        f"related-party transactions are disclosed in its annual report (Form 20-F, Items 6 and 7) instead."
    )


def get_fiscal_year_end_month(ticker: str) -> int:
    """
    Return the fiscal-year-end month (1-12) for a ticker.

    Defaults to 12 (calendar year end) for any ticker not in the known
    non-calendar-filer table — correct for the overwhelming majority of
    companies, so this never needs updating for "normal" filers.
    """
    if not ticker:
        return DEFAULT_FISCAL_YEAR_END_MONTH
    return TICKER_TO_FISCAL_YEAR_END_MONTH.get(ticker.lower(), DEFAULT_FISCAL_YEAR_END_MONTH)


def get_fiscal_period_end(ticker: str, fiscal_year: int) -> "datetime.date":
    """
    Approximate balance-sheet date for a company's fiscal year.

    Uses the last calendar day of the fiscal-year-end month, following the
    convention that FY{Y} ends in that month of Y (Sanofi FY2025 -> 31 Dec 2025,
    Microsoft FY2025 -> 30 Jun 2025). Exact 52/53-week year-end days (e.g. Apple's
    last Saturday of September) differ by a few days; FX rates move little over
    that, so the month-end is used.
    """
    import calendar
    from datetime import date as _date
    month = get_fiscal_year_end_month(ticker)
    last_day = calendar.monthrange(fiscal_year, month)[1]
    return _date(fiscal_year, month, last_day)


def get_most_recent_filed_fiscal_year(ticker: str, as_of: "datetime.date" = None) -> int:
    """
    Return the most recent fiscal year for which an annual report (10-K, or
    20-F for foreign private issuers) is likely to already be filed, given the company's fiscal-year-end month.

    Replaces the blanket `current_calendar_year - 1` assumption: a company
    whose fiscal year ends in a month that has already passed this calendar
    year has likely already filed that year's 10-K (companies get ~60-90
    days post fiscal-year-end to file, so we conservatively still lag by one
    extra year within ~4 months of the fiscal year end to avoid assuming an
    unfiled 10-K exists).
    """
    from datetime import date as _date
    today = as_of or _date.today()
    fye_month = get_fiscal_year_end_month(ticker)

    if fye_month == 12 and not is_foreign_private_issuer(ticker):
        # Calendar-year filer: this year's 10-K isn't filed until ~Feb-Mar
        # of next year, so "most recent filed" is always last calendar year.
        # (Foreign private issuers fall through to the lag-aware path below: a
        # 20-F is due 4 months after year end, so in Jan-Mar the year just
        # ended usually has no annual report yet.)
        return today.year - 1

    # Non-calendar filer: figure out which fiscal year most recently *ended*.
    # A fiscal year ending in month `fye_month` of year Y is "FY{Y}" (SEC
    # convention for these filers). If that fiscal-year-end has already
    # passed this calendar year, FY{today.year} already ended.
    fiscal_year_just_ended = today.year if today.month > fye_month else today.year - 1

    # Give a ~4 month filing-lag buffer before assuming the 10-K for that
    # fiscal year is actually filed.
    months_since_fye_end = (today.year - fiscal_year_just_ended) * 12 + (today.month - fye_month)
    if months_since_fye_end < 4:
        return fiscal_year_just_ended - 1
    return fiscal_year_just_ended


def _fiscal_quarter_end_months(fye_month: int) -> dict:
    """
    Return {quarter_number: expected_end_month} for a company with the given
    fiscal-year-end month. Fiscal quarters end 9/6/3/0 months before the
    fiscal year end (Q1 through Q4), wrapping around the 12-month calendar.
    """
    return {q: ((fye_month - (4 - q) * 3 - 1) % 12) + 1 for q in (1, 2, 3, 4)}


def get_fiscal_quarter(period_end_date, ticker: str = None):
    """
    Given an ACTUAL period-end date (e.g. from SEC EDGAR's reportDate, or
    cover-page text — never a guess), determine which fiscal quarter (1-4)
    of the ticker's fiscal year it corresponds to.

    This is ground-truth derivation, not approximation: real 10-Q period-end
    dates always fall in (or within a day or two of) one of the four
    expected quarter-end months for that company's fiscal calendar, so
    matching by month is reliable — the exact day varies (e.g. "Sunday
    closest to") but the month essentially never does.

    Args:
        period_end_date: a `date`/`datetime` object, or an ISO "YYYY-MM-DD" string.
        ticker: ticker symbol, used to look up the fiscal-year-end month.

    Returns:
        int (1-4) if determinable, else None.
    """
    if not period_end_date:
        return None

    if isinstance(period_end_date, str):
        try:
            from datetime import date as _date
            period_end_date = _date.fromisoformat(period_end_date[:10])
        except (ValueError, TypeError):
            return None

    fye_month = get_fiscal_year_end_month(ticker)
    quarter_end_months = _fiscal_quarter_end_months(fye_month)

    # Exact month match first.
    for quarter, expected_month in quarter_end_months.items():
        if period_end_date.month == expected_month:
            return quarter

    # Rare edge case: a period end date that lands in the day or two that
    # rolls into an adjacent month (e.g. a 52/53-week fiscal calendar whose
    # "Sunday closest to" lands on the 1st-2nd of the following month).
    # Pick the quarter whose expected month is closest (circularly).
    def _circular_month_distance(a, b):
        diff = abs(a - b) % 12
        return min(diff, 12 - diff)

    return min(quarter_end_months, key=lambda q: _circular_month_distance(period_end_date.month, quarter_end_months[q]))


def get_fiscal_quarter_calendar_span(ticker: str, fiscal_quarter: int) -> str:
    """
    Human-readable calendar month range a ticker's given fiscal quarter
    covers, e.g. "October-December" for Apple's fiscal Q1. Used to explain
    to the LLM (and ultimately the user) when two companies' same-numbered
    fiscal quarter are NOT the same calendar period — the actual point of
    tracking fiscal calendars at all.
    """
    import calendar
    fye_month = get_fiscal_year_end_month(ticker)
    end_month = _fiscal_quarter_end_months(fye_month)[fiscal_quarter]
    start_month = ((end_month - 3) % 12) + 1  # 3-month span ending at end_month
    return f"{calendar.month_name[start_month]}-{calendar.month_name[end_month]}"


def get_ticker(company_name: str) -> str:
    """Get ticker symbol from company name with fuzzy/partial matching."""
    if not company_name:
        return ""
        
    name_lower = company_name.lower().strip()
    
    # 1. Exact match in reverse mapping
    if name_lower in COMPANY_TO_TICKER:
        ticker = COMPANY_TO_TICKER[name_lower]
        # Clean up internal aliases like 'xom_alias' -> 'xom'
        return ticker.split('_')[0]
        
    # 2. The input is already a known ticker. Portfolios store tickers (e.g. "HD"),
    # not names, and without this a short ticker falls through to the substring
    # match below and gets captured by an unrelated company whose name merely
    # contains it ("hd" in "hdfc bank").
    if name_lower in TICKER_TO_COMPANY:
        return name_lower

    # 3. Partial match: search all company names
    for ticker, company in TICKER_TO_COMPANY.items():
        comp_lower = company.lower()
        # If the input name is in the full company name (e.g. "Exxon Mobil" in "Exxon Mobil Corporation"),
        # matched on whole words only so a short input can't hit the middle of a
        # word ("gs" in "gsk", "dis" in "nordisk", "bac" in "tobacco").
        # Or if the full company name is in the input name.
        if re.search(r'\b' + re.escape(name_lower) + r'\b', comp_lower) or comp_lower in name_lower:
            return ticker.split('_')[0]

    return ""
