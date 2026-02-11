"""
Mapping of stock tickers to company names and utility functions.
"""

TICKER_TO_COMPANY = {
    # Tech companies
    'aapl': 'apple',
    'msft': 'microsoft',
    'googl': 'alphabet',
    'goog': 'alphabet',
    'amzn': 'amazon',
    'nvda': 'nvidia',
    'tsla': 'tesla',
    'meta': 'meta',
    'fb': 'meta',
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
    'zoom': 'zoom',
    'twlo': 'twilio',
    'coin': 'coinbase',
    'pypl': 'paypal',
    'sqm': 'square',
    'snps': 'synopsys',
    'cdns': 'cadence',
    
    # Financial companies
    'jpm': 'jpmorgan',
    'bac': 'bankofamerica',
    'gs': 'goldman',
    'ms': 'morgan',
    'wu': 'western',
    'v': 'visa',
    'ma': 'mastercard',
    'axa': 'axa',
    'axp': 'amex',
    'c': 'citigroup',
    'wfc': 'wells',
    'tfc': 'truist',
    'bnc': 'banco',
    
    # Healthcare
    'jnj': 'johnson',
    'pfe': 'pfizer',
    'mrna': 'moderna',
    'abbv': 'abbvie',
    'ulvr': 'unilever',
    'cvx': 'chevron',
    'xom': 'exxon',
    'cop': 'conocophillips',
    'slb': 'schlumberger',
    
    # Energy
    'psa': 'peugeot',
    'bp': 'bp',
    'rds': 'royaldutch',
    'shel': 'shell',
    
    # Retail & Consumer
    'wmt': 'walmart',
    'hd': 'homedepot',
    'ko': 'coca',
    'pep': 'pepsi',
    'mcd': 'mcdonalds',
    'nke': 'nike',
    'lulu': 'lululemon',
    'nvr': 'nvr',
    'hm': 'hmgroup',
    'pm': 'philipmorris',
    'mbb': 'mutual',
    
    # Automotive
    'gm': 'generalmotors',
    'f': 'ford',
    'gme': 'gamestop',
    'tslaq': 'tesla',
    
    # Industrial
    'ba': 'boeing',
    'ge': 'ge',
    'cat': 'caterpillar',
    'hog': 'harley',
    'axp': 'americanexpress',
    
    # Real Estate
    'vno': 'vornado',
    'spg': 'simon',
    'pld': 'prologis',
}

# Reverse mapping for looking up ticker by company name
COMPANY_TO_TICKER = {v: k for k, v in TICKER_TO_COMPANY.items()}

def get_company_name(ticker: str) -> str:
    """Get company name from ticker symbol."""
    if not ticker:
        return ""
    return TICKER_TO_COMPANY.get(ticker.lower(), ticker.lower())

def get_ticker(company_name: str) -> str:
    """Get ticker symbol from company name."""
    if not company_name:
        return ""
    return COMPANY_TO_TICKER.get(company_name.lower(), "")
