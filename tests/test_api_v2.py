"""
Integration tests for Agentic RAG API v2.0

Run with: pytest tests/test_api_v2.py -v
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.models import Base
from database.connection import get_db_session
from app_v2 import app

# Test database
TEST_DATABASE_URL = "sqlite:///./test_portfolios.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing"""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Override dependency
app.dependency_overrides[get_db_session] = override_get_db

# Create test client
client = TestClient(app)


@pytest.fixture(scope="function", autouse=True)
def setup_database():
    """Create and tear down test database for each test"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


class TestPortfolioManagement:
    """Test portfolio CRUD operations"""
    
    def test_create_portfolio(self):
        """Test creating a new portfolio"""
        response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["apple", "microsoft"],
            "description": "Test description"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Portfolio"
        assert len(data["company_names"]) == 2
        assert "apple" in data["company_names"]
    
    def test_get_portfolio(self):
        """Test retrieving a portfolio"""
        # Create portfolio
        create_response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["apple"]
        })
        portfolio_id = create_response.json()["id"]
        
        # Get portfolio
        response = client.get(f"/portfolios/{portfolio_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == portfolio_id
        assert data["name"] == "Test Portfolio"
    
    def test_get_user_portfolios(self):
        """Test getting all portfolios for a user"""
        # Create multiple portfolios
        client.post("/portfolios/", json={
            "user_id": "user1",
            "name": "Portfolio 1",
            "company_names": ["apple"]
        })
        client.post("/portfolios/", json={
            "user_id": "user1",
            "name": "Portfolio 2",
            "company_names": ["microsoft"]
        })
        client.post("/portfolios/", json={
            "user_id": "user2",
            "name": "Portfolio 3",
            "company_names": ["google"]
        })
        
        # Get portfolios for user1
        response = client.get("/portfolios/user/user1")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
    
    def test_update_portfolio(self):
        """Test updating a portfolio"""
        # Create portfolio
        create_response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Original Name",
            "company_names": ["apple"]
        })
        portfolio_id = create_response.json()["id"]
        
        # Update portfolio
        response = client.put(f"/portfolios/{portfolio_id}", json={
            "name": "Updated Name",
            "company_names": ["apple", "microsoft"]
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert len(data["company_names"]) == 2
    
    def test_delete_portfolio(self):
        """Test deleting a portfolio"""
        # Create portfolio
        create_response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["apple"]
        })
        portfolio_id = create_response.json()["id"]
        
        # Delete portfolio
        response = client.delete(f"/portfolios/{portfolio_id}")
        assert response.status_code == 200
        
        # Verify deletion
        get_response = client.get(f"/portfolios/{portfolio_id}")
        assert get_response.status_code == 404


class TestSessionManagement:
    """Test session operations"""
    
    def test_create_session(self):
        """Test creating a session for a portfolio"""
        # Create portfolio
        portfolio_response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["apple"]
        })
        portfolio_id = portfolio_response.json()["id"]
        
        # Create session
        response = client.post("/portfolios/sessions", json={
            "portfolio_id": portfolio_id,
            "user_id": "test_user"
        })
        assert response.status_code == 200
        data = response.json()
        assert "thread_id" in data
        assert data["portfolio_id"] == portfolio_id
    
    def test_get_session(self):
        """Test retrieving a session"""
        # Create portfolio and session
        portfolio_response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["apple"]
        })
        portfolio_id = portfolio_response.json()["id"]
        
        session_response = client.post("/portfolios/sessions", json={
            "portfolio_id": portfolio_id,
            "user_id": "test_user"
        })
        thread_id = session_response.json()["thread_id"]
        
        # Get session
        response = client.get(f"/portfolios/sessions/{thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == thread_id
        assert data["portfolio_id"] == portfolio_id
    
    def test_session_not_found(self):
        """Test getting non-existent session"""
        response = client.get("/portfolios/sessions/nonexistent_thread")
        assert response.status_code == 404


class TestHealthCheck:
    """Test health and status endpoints"""
    
    def test_root_endpoint(self):
        """Test root endpoint"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "endpoints" in data
    
    def test_health_check(self):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


class TestDataValidation:
    """Test input validation"""
    
    def test_create_portfolio_missing_fields(self):
        """Test creating portfolio with missing required fields"""
        response = client.post("/portfolios/", json={
            "user_id": "test_user"
            # Missing name and company_names
        })
        assert response.status_code == 422
    
    def test_create_portfolio_empty_companies(self):
        """Test creating portfolio with empty company list"""
        response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test",
            "company_names": []
        })
        # Should succeed - empty list is valid, just not useful
        assert response.status_code == 200
    
    def test_create_session_invalid_portfolio(self):
        """Test creating session with non-existent portfolio"""
        response = client.post("/portfolios/sessions", json={
            "portfolio_id": 99999,
            "user_id": "test_user"
        })
        assert response.status_code == 404


class TestCompanyNameNormalization:
    """Test that company names are normalized to lowercase"""
    
    def test_company_names_normalized(self):
        """Test that company names are stored in lowercase"""
        response = client.post("/portfolios/", json={
            "user_id": "test_user",
            "name": "Test Portfolio",
            "company_names": ["Apple", "MICROSOFT", "Google"]
        })
        assert response.status_code == 200
        data = response.json()
        assert all(name.islower() for name in data["company_names"])
        assert "apple" in data["company_names"]
        assert "microsoft" in data["company_names"]


# Note: RAG endpoint tests (/ask and /compare) require the full graph
# to be initialized, which depends on external services (Qdrant, OpenAI, etc.)
# These should be tested in separate integration tests with proper setup.
