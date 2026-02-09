#!/bin/bash

# =============================================================================
# Investment Analyst API Startup Script
# =============================================================================
# This script starts the main Investment Analyst API server
# Optionally starts MCP servers if they're not running
#
# Usage:
#   ./start_api.sh                 # Start API only
#   ./start_api.sh --with-mcp      # Start MCP servers + API
#   ./start_api.sh --dev           # Development mode with reload
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/venv"
API_PORT=8000

# Parse arguments
WITH_MCP=false
DEV_MODE=false

for arg in "$@"; do
    case $arg in
        --with-mcp)
            WITH_MCP=true
            shift
            ;;
        --dev)
            DEV_MODE=true
            shift
            ;;
        --help|-h)
            cat << EOF
${BLUE}Investment Analyst API Startup Script${NC}

${YELLOW}Usage:${NC}
  ./start_api.sh [options]

${YELLOW}Options:${NC}
  --with-mcp    Start MCP servers before starting API
  --dev         Development mode (auto-reload on code changes)
  --help        Show this help message

${YELLOW}Examples:${NC}
  ./start_api.sh                    # Start API only
  ./start_api.sh --with-mcp         # Start MCP + API
  ./start_api.sh --dev              # Dev mode with reload
  ./start_api.sh --with-mcp --dev   # Full dev environment

${YELLOW}Services:${NC}
  Main API:      http://localhost:8000
  API Docs:      http://localhost:8000/docs
  Health Check:  http://localhost:8000/health

${YELLOW}MCP Servers (optional):${NC}
  Stock Info:    http://localhost:8565
  Technical:     http://localhost:8566
  Research:      http://localhost:8567

EOF
            exit 0
            ;;
    esac
done

# Print functions
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"
}

# Check if virtual environment exists
check_venv() {
    if [ ! -d "$VENV_PATH" ]; then
        print_error "Virtual environment not found at $VENV_PATH"
        print_info "Creating virtual environment..."
        python3 -m venv "$VENV_PATH"
        print_success "Virtual environment created"
    fi
}

# Check if port is in use
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Check dependencies
check_dependencies() {
    source "$VENV_PATH/bin/activate"
    
    print_info "Checking dependencies..."
    
    if ! python -c "import fastapi" 2>/dev/null; then
        print_warning "Dependencies not installed. Installing..."
        pip install -r "$PROJECT_ROOT/requirements.txt"
        print_success "Dependencies installed"
    else
        print_success "Dependencies check passed"
    fi
}

# Check environment file
check_env() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        print_warning ".env file not found"
        if [ -f "$PROJECT_ROOT/.env.example" ]; then
            print_info "Creating .env from .env.example..."
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
            print_warning "Please edit .env file with your API keys before starting the server"
            exit 1
        else
            print_error "No .env or .env.example file found"
            exit 1
        fi
    fi
}

# Start MCP servers if requested
start_mcp_servers() {
    if [ "$WITH_MCP" = true ]; then
        print_header "Starting MCP Servers"
        
        if [ -f "$SCRIPT_DIR/start_mcp_servers.sh" ]; then
            "$SCRIPT_DIR/start_mcp_servers.sh" start
        else
            print_warning "MCP startup script not found at $SCRIPT_DIR/start_mcp_servers.sh"
            print_info "MCP servers will need to be started manually for full stock analysis features"
        fi
        
        echo ""
        sleep 2
    fi
}

# Check MCP server status
check_mcp_status() {
    local stock_info_running=false
    local technical_running=false
    local research_running=false
    
    check_port 8565 && stock_info_running=true
    check_port 8566 && technical_running=true
    check_port 8567 && research_running=true
    
    if $stock_info_running && $technical_running && $research_running; then
        print_success "All MCP servers are running"
        print_info "Full stock analysis features available"
    else
        print_warning "Some or all MCP servers are not running"
        print_info "Stock analysis will work with limited functionality"
        print_info "To start MCP servers: ./scripts/start_mcp_servers.sh"
    fi
}

# Start the API server
start_api() {
    print_header "Starting Investment Analyst API"
    
    # Check if API is already running
    if check_port $API_PORT; then
        print_error "Port $API_PORT is already in use"
        print_info "Stop the existing server or use a different port"
        exit 1
    fi
    
    # Activate virtual environment
    source "$VENV_PATH/bin/activate"
    
    # Navigate to project root
    cd "$PROJECT_ROOT"
    
    print_success "Starting server on http://localhost:$API_PORT"
    echo ""
    print_info "📚 API Documentation: http://localhost:$API_PORT/docs"
    print_info "🏥 Health Check: http://localhost:$API_PORT/health"
    print_info "💡 Web UI: Open static/index.html in browser"
    echo ""
    
    # Start server
    if [ "$DEV_MODE" = true ]; then
        print_info "Development mode: Auto-reload enabled"
        echo ""
        python -m uvicorn app.main:app --reload --port $API_PORT --host 0.0.0.0
    else
        print_info "Production mode"
        echo ""
        python -m uvicorn app.main:app --port $API_PORT --host 0.0.0.0
    fi
}

# Main execution
main() {
    print_header "Investment Analyst API - Startup"
    
    # Pre-flight checks
    check_venv
    check_env
    check_dependencies
    
    echo ""
    
    # Start MCP servers if requested
    start_mcp_servers
    
    # Check MCP server status
    check_mcp_status
    
    echo ""
    
    # Start API server
    start_api
}

# Run main
main
