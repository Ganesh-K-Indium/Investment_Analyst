"""
Mapping of stock tickers to company names and utility functions.
"""

TICKER_TO_COMPANY = {
    # Tech companies
    'aapl': 'apple',
    'msft': 'microsoft',
    'googl': 'alphabet',
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
    'tslaq': 'tesla',
    
    # Industrial
    'ba': 'boeing',
    'ge': 'general electric',
    'cat': 'caterpillar',
    'hog': 'harley davidson',
    
    # Real Estate
    'vno': 'vornado realty trust',
    'spg': 'simon property group',
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
    """Get ticker symbol from company name with fuzzy/partial matching."""
    if not company_name:
        return ""
        
    name_lower = company_name.lower().strip()
    
    # 1. Exact match in reverse mapping
    if name_lower in COMPANY_TO_TICKER:
        ticker = COMPANY_TO_TICKER[name_lower]
        # Clean up internal aliases like 'xom_alias' -> 'xom'
        return ticker.split('_')[0]
        
    # 2. Partial match: search all company names
    for ticker, company in TICKER_TO_COMPANY.items():
        comp_lower = company.lower()
        # If the input name is in the full company name (e.g. "Exxon Mobil" in "Exxon Mobil Corporation")
        # Or if the full company name is in the input name
        if name_lower in comp_lower or comp_lower in name_lower:
            return ticker.split('_')[0]
            
    return ""
