#!/usr/bin/env python3
"""
Delete one or more Qdrant collections by ticker symbol.

Usage:
    python delete_collection.py <TICKER1> [TICKER2] [TICKER3] ...
    python delete_collection.py --list

Examples:
    python delete_collection.py AVGO
    python delete_collection.py AVGO BRK
"""

import sys
import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient

# Load environment variables
load_dotenv()


def get_client():
    """Connect to Qdrant."""
    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")

    if not url or not api_key:
        print("❌ Error: QDRANT_URL and QDRANT_API_KEY must be set in .env")
        sys.exit(1)

    return QdrantClient(url=url, api_key=api_key)


def list_collections(client):
    """List all collections with their point counts."""
    collections = client.get_collections().collections
    if not collections:
        print("No collections found in the database.")
        return

    print("\n📦 All collections in Qdrant:")
    print("-" * 50)
    for c in collections:
        try:
            info = client.get_collection(c.name)
            print(f"  • {c.name}: {info.points_count} points")
        except Exception:
            print(f"  • {c.name}: (unable to get info)")
    print("-" * 50)


def delete_collections(client, tickers):
    """Delete collections for multiple ticker symbols."""
    existing = [c.name for c in client.get_collections().collections]
    
    # Show what will be deleted
    found = []
    not_found = []
    total_points = 0
    
    for ticker in tickers:
        collection_name = f"ticker_{ticker.lower()}"
        if collection_name in existing:
            info = client.get_collection(collection_name)
            found.append((collection_name, info.points_count))
            total_points += info.points_count
        else:
            not_found.append(collection_name)
    
    if not_found:
        print(f"\n⚠️  Not found (skipping): {', '.join(not_found)}")
    
    if not found:
        print("\n❌ No matching collections to delete.")
        return
    
    print(f"\n🗑️  About to delete {len(found)} collection(s):")
    for name, count in found:
        print(f"   • {name}: {count} points")
    print(f"   Total points to delete: {total_points}")
    
    confirm = input(f"\n⚠️  Are you sure you want to delete ALL of the above? (yes/no): ").strip().lower()
    
    if confirm == "yes":
        for name, count in found:
            client.delete_collection(name)
            print(f"   ✅ Deleted '{name}' ({count} points)")
        print(f"\n✅ All {len(found)} collection(s) deleted successfully!")
    else:
        print("\n🚫 Deletion cancelled.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python delete_collection.py <TICKER1> [TICKER2] ...")
        print("       python delete_collection.py --list")
        print("\nExamples:")
        print("  python delete_collection.py AVGO")
        print("  python delete_collection.py AVGO BRK")
        sys.exit(1)

    client = get_client()

    if sys.argv[1] == "--list":
        list_collections(client)
    else:
        tickers = sys.argv[1:]
        list_collections(client)
        delete_collections(client, tickers)


if __name__ == "__main__":
    main()
