#!/bin/bash

# Agentic RAG API Server Startup Script
# Run from project root directory

echo "========================================="
echo "🚀 Starting Agentic RAG API Server"
echo "========================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    echo "Please create one first:"
    echo "  python -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
echo "📦 Activating virtual environment..."
source venv/bin/activate

# Check if requirements are installed
if ! python -c "import fastapi" 2>/dev/null; then
    echo "⚠️  Dependencies not installed!"
    echo "Installing requirements..."
    pip install -r requirements.txt
fi

# Start the server from root directory
echo ""
echo "🚀 Starting server..."
echo "📍 Server will be available at: http://localhost:8000"
echo "📚 API Documentation: http://localhost:8000/docs"
echo "🖥️  UI: Open static/index.html in your browser"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run from root with correct module path: app.main:app
python -m uvicorn app.main:app --reload --reload-dir app --reload-dir rag --reload-dir quant --reload-dir schemas --reload-dir ingestion --port 8000
