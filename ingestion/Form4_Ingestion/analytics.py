"""
analytics.py
------------
Stub implementation to prevent ImportErrors in ingest.py.
"""

class TransactionAnalytics:
    def __init__(self, db):
        self.db = db

    def print_summary(self, ticker=None):
        print(f"Analytics summary for {ticker} (Stub)")
