#!/usr/bin/env bash

# =============================================================================
# MCP Servers Startup Script for Investment Analyst API
# =============================================================================
# This script starts the 3 MCP (Model Context Protocol) servers required for
# the Stock Analysis system:
#   1. Stock Information Server (port 8565) - yahoo-finance-mcp
#   2. Technical Analysis Server (port 8566) - Stock_Analysis
#   3. Research Server (port 8567) - research_mcp
#
# Usage:
#   ./start_mcp_servers.sh        # Start all servers
#   ./start_mcp_servers.sh stop   # Stop all servers
#   ./start_mcp_servers.sh status # Check server status
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/venv"
QUANT_DIR="$PROJECT_ROOT/quant"
LOG_DIR="$PROJECT_ROOT/logs/mcp"
PID_DIR="$PROJECT_ROOT/logs/mcp/pids"

# Server configurations (compatible with bash 3.2+)
# Format: name|directory|script|port
SERVER_CONFIGS=(
    "stock_info|yahoo-finance-mcp|server.py|8565"
    "technical|Stock_Analysis|server_mcp.py|8566"
    "research|research_mcp|server_mcp.py|8567"
)

# Function to print colored output
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

# Function to check if a port is in use
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

# Function to check if virtual environment exists
check_venv() {
    if [ ! -d "$VENV_PATH" ]; then
        print_error "Virtual environment not found at $VENV_PATH"
        print_info "Please create it with: python -m venv venv"
        exit 1
    fi
}

# Function to create log directories
setup_log_dirs() {
    mkdir -p "$LOG_DIR"
    mkdir -p "$PID_DIR"
    print_success "Log directories ready at $LOG_DIR"
}

# Function to activate virtual environment
activate_venv() {
    source "$VENV_PATH/bin/activate"
    print_success "Virtual environment activated"
}

# Function to start a single MCP server
start_server() {
    local config=$1
    IFS='|' read -r name dir script port <<< "$config"
    
    local server_dir="$QUANT_DIR/$dir"
    local server_script="$server_dir/$script"
    local log_file="$LOG_DIR/${name}.log"
    local pid_file="$PID_DIR/${name}.pid"
    
    print_info "Starting $name server..."
    
    # Check if directory exists
    if [ ! -d "$server_dir" ]; then
        print_error "Server directory not found: $server_dir"
        return 1
    fi
    
    # Check if script exists
    if [ ! -f "$server_script" ]; then
        print_error "Server script not found: $server_script"
        return 1
    fi
    
    # Check if port is already in use
    if check_port $port; then
        print_warning "$name server already running on port $port"
        return 0
    fi
    
    # Start the server
    cd "$server_dir"
    nohup python "$script" > "$log_file" 2>&1 &
    local pid=$!
    echo $pid > "$pid_file"
    
    # Wait a moment and check if server started
    sleep 2
    if ps -p $pid > /dev/null 2>&1; then
        print_success "$name server started (PID: $pid, Port: $port, Log: $log_file)"
        return 0
    else
        print_error "$name server failed to start. Check logs at $log_file"
        return 1
    fi
}

# Function to stop a single MCP server
stop_server() {
    local config=$1
    IFS='|' read -r name dir script port <<< "$config"
    local pid_file="$PID_DIR/${name}.pid"
    
    print_info "Stopping $name server..."
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p $pid > /dev/null 2>&1; then
            kill $pid
            sleep 1
            if ps -p $pid > /dev/null 2>&1; then
                kill -9 $pid
            fi
            print_success "$name server stopped (PID: $pid)"
        else
            print_warning "$name server not running (stale PID file)"
        fi
        rm -f "$pid_file"
    else
        # Try to kill by port
        if check_port $port; then
            local pid=$(lsof -ti:$port)
            kill $pid 2>/dev/null || kill -9 $pid 2>/dev/null
            print_success "$name server stopped (Port: $port)"
        else
            print_warning "$name server not running"
        fi
    fi
}

# Function to check status of a single server
check_server_status() {
    local config=$1
    IFS='|' read -r name dir script port <<< "$config"
    local pid_file="$PID_DIR/${name}.pid"
    
    if check_port $port; then
        local pid=$(lsof -ti:$port)
        echo -e "${GREEN}●${NC} $name server: ${GREEN}RUNNING${NC} (PID: $pid, Port: $port)"
        return 0
    else
        echo -e "${RED}○${NC} $name server: ${RED}STOPPED${NC} (Port: $port)"
        return 1
    fi
}

# Function to start all servers
start_all() {
    print_header "Starting MCP Servers for Stock Analysis"
    
    check_venv
    setup_log_dirs
    activate_venv
    
    local success_count=0
    local total_count=${#SERVER_CONFIGS[@]}
    
    for config in "${SERVER_CONFIGS[@]}"; do
        if start_server "$config"; then
            ((success_count++))
        fi
        echo ""
    done
    
    echo ""
    print_header "Startup Summary"
    
    if [ $success_count -eq $total_count ]; then
        print_success "All $total_count MCP servers started successfully!"
        echo ""
        print_info "You can now start the main API server with:"
        echo -e "  ${YELLOW}python -m uvicorn app.main:app --reload${NC}"
        echo ""
        print_info "Logs available at: $LOG_DIR"
        return 0
    else
        print_warning "$success_count/$total_count servers started successfully"
        print_info "Check logs at: $LOG_DIR"
        return 1
    fi
}

# Function to stop all servers
stop_all() {
    print_header "Stopping MCP Servers"
    
    for config in "${SERVER_CONFIGS[@]}"; do
        stop_server "$config"
    done
    
    echo ""
    print_success "All MCP servers stopped"
}

# Function to show status of all servers
show_status() {
    print_header "MCP Servers Status"
    
    local running_count=0
    local total_count=${#SERVER_CONFIGS[@]}
    
    for config in "${SERVER_CONFIGS[@]}"; do
        if check_server_status "$config"; then
            ((running_count++))
        fi
    done
    
    echo ""
    if [ $running_count -eq $total_count ]; then
        print_success "All $total_count servers are running"
    elif [ $running_count -eq 0 ]; then
        print_warning "All servers are stopped"
    else
        print_warning "$running_count/$total_count servers running"
    fi
    
    echo ""
    print_info "Check detailed logs at: $LOG_DIR"
}

# Function to tail logs
tail_logs() {
    print_header "MCP Server Logs (Press Ctrl+C to exit)"
    
    if [ ! -d "$LOG_DIR" ]; then
        print_error "No logs directory found at $LOG_DIR"
        exit 1
    fi
    
    tail -f "$LOG_DIR"/*.log
}

# Function to show help
show_help() {
    cat << EOF
${BLUE}MCP Servers Startup Script${NC}

${YELLOW}Usage:${NC}
  ./start_mcp_servers.sh [command]

${YELLOW}Commands:${NC}
  start      Start all MCP servers (default)
  stop       Stop all MCP servers
  restart    Restart all MCP servers
  status     Check status of all servers
  logs       Tail server logs
  help       Show this help message

${YELLOW}Servers:${NC}
  1. Stock Information (port 8565) - yahoo-finance-mcp
  2. Technical Analysis (port 8566) - Stock_Analysis  
  3. Research (port 8567) - research_mcp

${YELLOW}Examples:${NC}
  ./start_mcp_servers.sh              # Start all servers
  ./start_mcp_servers.sh stop         # Stop all servers
  ./start_mcp_servers.sh status       # Check status
  ./start_mcp_servers.sh logs         # View logs

${YELLOW}Files:${NC}
  Logs: $LOG_DIR/
  PIDs: $PID_DIR/

EOF
}

# Main script logic
main() {
    local command=${1:-start}
    
    case $command in
        start)
            start_all
            ;;
        stop)
            stop_all
            ;;
        restart)
            stop_all
            sleep 2
            start_all
            ;;
        status)
            show_status
            ;;
        logs)
            tail_logs
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
