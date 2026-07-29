"""
analytics.py
------------
Stub implementation to prevent ImportErrors in ingest.py.
"""

import logging

logger = logging.getLogger("Form4Ingestion.analytics")


class TransactionAnalytics:
    def __init__(self, db):
        self.db = db

    def print_summary(self, ticker=None):
        logger.info("Analytics summary for %s (Stub)", ticker)
