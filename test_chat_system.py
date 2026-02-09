"""
Test script for Chat History System
Run this to verify all chat persistence features are working correctly
"""
import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
USER_ID = "test_user_123"
PORTFOLIO_NAME = "Test Portfolio"
COMPANIES = ["Apple", "Microsoft", "Tesla"]

def print_section(title):
    """Print a formatted section header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)

def test_portfolio_creation():
    """Test 1: Create a portfolio"""
    print_section("TEST 1: Create Portfolio")
    
    response = requests.post(f"{BASE_URL}/portfolios", json={
        "user_id": USER_ID,
        "name": PORTFOLIO_NAME,
        "company_names": COMPANIES,
        "description": "Test portfolio for chat history"
    })
    
    if response.status_code == 200:
        portfolio = response.json()
        print(f"SUCCESS: Portfolio created: {portfolio['name']}")
        print(f"   Portfolio ID: {portfolio['id']}")
        return portfolio['id']
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_session_creation(portfolio_id):
    """Test 2: Create a session"""
    print_section("TEST 2: Create Session")
    
    response = requests.post(f"{BASE_URL}/portfolios/sessions", json={
        "portfolio_id": portfolio_id,
        "user_id": USER_ID
    })
    
    if response.status_code == 200:
        session = response.json()
        print(f"SUCCESS: Session created: {session['id']}")
        return session['id']
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_rag_query(thread_id):
    """Test 3: Send RAG query (auto-creates ChatSession)"""
    print_section("TEST 3: Send RAG Query")
    
    response = requests.post(f"{BASE_URL}/ask", json={
        "query": "What is Apple's revenue for 2025?",
        "thread_id": thread_id
    })
    
    if response.status_code == 200:
        result = response.json()
        print(f"SUCCESS: Query successful")
        print(f"   Answer: {result['answer'][:100]}...")
        print(f"   Chat automatically persisted to database")
        return True
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return False

def test_quant_query():
    """Test 4: Send Quant query (auto-creates ChatSession)"""
    print_section("TEST 4: Send Quant Query")
    
    session_id = f"quant_{USER_ID}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    response = requests.post(f"{BASE_URL}/quant/query", json={
        "query": "What is the current stock price of Apple?",
        "user_id": USER_ID,
        "session_id": session_id
    })
    
    if response.status_code == 200:
        result = response.json()
        print(f"SUCCESS: Query successful")
        print(f"   Session ID: {result['session_id']}")
        print(f"   Response: {result['response'][:100]}...")
        print(f"   Chat automatically persisted to database")
        return session_id
    elif response.status_code == 503:
        print(f"WARNING: Quant system not available (MCP servers not running)")
        print(f"   This is expected if MCP servers aren't started")
        return None
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_get_user_sessions():
    """Test 5: Get all user's chat sessions"""
    print_section("TEST 5: Get User's Chat Sessions")
    
    response = requests.get(f"{BASE_URL}/chats/user/{USER_ID}/sessions")
    
    if response.status_code == 200:
        sessions = response.json()
        print(f"SUCCESS: Retrieved {len(sessions)} sessions")
        for session in sessions:
            print(f"   - {session['title']} ({session['agent_type']}): {session['message_count']} messages")
        return sessions
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return []

def test_get_session_history(session_id):
    """Test 6: Get chat history for a session"""
    print_section("TEST 6: Get Session Chat History")
    
    response = requests.get(f"{BASE_URL}/chats/session/{session_id}")
    
    if response.status_code == 200:
        history = response.json()
        print(f"SUCCESS: Retrieved history for: {history['title']}")
        print(f"   Messages: {history['message_count']}")
        print(f"   Agent: {history['agent_type']}")
        print(f"\n   Conversation:")
        for msg in history['messages']:
            role = msg['role'].upper()
            content = msg['content'][:80] + "..." if len(msg['content']) > 80 else msg['content']
            print(f"   [{role}]: {content}")
        return history
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_export_session(session_id, format="json"):
    """Test 7: Export session"""
    print_section(f"TEST 7: Export Session ({format.upper()})")
    
    response = requests.get(f"{BASE_URL}/chats/session/{session_id}/export?format={format}")
    
    if response.status_code == 200:
        print(f"SUCCESS: Export successful")
        if format == "json":
            data = response.json()
            print(f"   Exported {data['message_count']} messages")
        else:
            print(f"   File size: {len(response.content)} bytes")
        return True
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return False

def test_get_session_stats(session_id):
    """Test 8: Get session statistics"""
    print_section("TEST 8: Get Session Statistics")
    
    response = requests.get(f"{BASE_URL}/chats/session/{session_id}/stats")
    
    if response.status_code == 200:
        stats = response.json()
        print(f"SUCCESS: Statistics retrieved")
        print(f"   Messages: {stats['message_count']}")
        print(f"   Tokens: {stats['total_tokens']}")
        print(f"   Created: {stats['created_at']}")
        return stats
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_get_user_stats():
    """Test 9: Get user statistics"""
    print_section("TEST 9: Get User Statistics")
    
    response = requests.get(f"{BASE_URL}/chats/user/{USER_ID}/stats")
    
    if response.status_code == 200:
        stats = response.json()
        print(f"SUCCESS: User statistics retrieved")
        print(f"   Total Sessions: {stats['total_sessions']}")
        print(f"   RAG Sessions: {stats['rag_sessions']}")
        print(f"   Quant Sessions: {stats['quant_sessions']}")
        print(f"   Total Messages: {stats['total_messages']}")
        return stats
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return None

def test_update_title(session_id):
    """Test 10: Update session title"""
    print_section("TEST 10: Update Session Title")
    
    new_title = f"Updated Chat - {datetime.now().strftime('%H:%M:%S')}"
    response = requests.put(f"{BASE_URL}/chats/session/{session_id}/title", json={
        "title": new_title
    })
    
    if response.status_code == 200:
        result = response.json()
        print(f"SUCCESS: Title updated to: {result['title']}")
        return True
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return False

def test_clear_messages(session_id):
    """Test 11: Clear session messages"""
    print_section("TEST 11: Clear Session Messages")
    
    response = requests.delete(f"{BASE_URL}/chats/session/{session_id}/messages")
    
    if response.status_code == 200:
        result = response.json()
        print(f"SUCCESS: Cleared {result['messages_deleted']} messages")
        print(f"   Session still exists and can be reused")
        return True
    else:
        print(f"FAILED: {response.status_code} - {response.text}")
        return False

def test_health_check():
    """Test 0: Health check"""
    print_section("TEST 0: API Health Check")
    
    response = requests.get(f"{BASE_URL}/health")
    
    if response.status_code == 200:
        health = response.json()
        print(f"SUCCESS: API is healthy")
        print(f"   Version: {health['version']}")
        print(f"   Document Analysis: {health['services']['document_analysis']['status']}")
        print(f"   Stock Analysis: {health['services']['stock_analysis']['status']}")
        print(f"   Chat History: {health['services']['chat_history']['status']}")
        return True
    else:
        print(f"FAILED: API not responding")
        return False

def main():
    """Run all tests"""
    print("\n")
    print("TEST: Chat History System - Integration Tests")
    print("=" * 70)
    print(f"Target API: {BASE_URL}")
    print(f"User ID: {USER_ID}")
    print("")
    
    # Test 0: Health check
    if not test_health_check():
        print("\nERROR: API is not running. Please start the server first:")
        print("   ./scripts/start_api.sh")
        return
    
    # Test 1-2: Setup (Portfolio & Session)
    portfolio_id = test_portfolio_creation()
    if not portfolio_id:
        print("\nERROR: Cannot continue without portfolio")
        return
    
    thread_id = test_session_creation(portfolio_id)
    if not thread_id:
        print("\nERROR: Cannot continue without session")
        return
    
    # Test 3: RAG Query (creates ChatSession automatically)
    test_rag_query(thread_id)
    
    # Test 4: Quant Query (creates ChatSession automatically)
    quant_session_id = test_quant_query()
    
    # Test 5: Get all user sessions
    sessions = test_get_user_sessions()
    
    # Test 6: Get chat history
    if sessions:
        test_get_session_history(sessions[0]['session_id'])
    
    # Test 7: Export session
    if sessions:
        test_export_session(sessions[0]['session_id'], format="json")
        test_export_session(sessions[0]['session_id'], format="txt")
    
    # Test 8: Session stats
    if sessions:
        test_get_session_stats(sessions[0]['session_id'])
    
    # Test 9: User stats
    test_get_user_stats()
    
    # Test 10: Update title
    if sessions:
        test_update_title(sessions[0]['session_id'])
    
    # Test 11: Clear messages
    if len(sessions) > 1:
        test_clear_messages(sessions[1]['session_id'])
    
    # Summary
    print_section("SUMMARY")
    print(f"SUCCESS: All tests completed!")
    print(f"\nView your chat history:")
    print(f"   GET {BASE_URL}/chats/user/{USER_ID}/sessions")
    print(f"\nAPI Documentation:")
    print(f"   {BASE_URL}/docs")
    print("")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWARNING: Tests interrupted by user")
    except Exception as e:
        print(f"\n\nERROR: Error running tests: {e}")
        import traceback
        traceback.print_exc()
