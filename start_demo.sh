#!/usr/bin/env bash
# start_demo.sh — Start the OmniVoice mock server and open the demo in your browser.
# Usage:  bash start_demo.sh
# Requires: Python 3 (stdlib only — no pip install needed)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_SERVER="$SCRIPT_DIR/demo/mock_server.py"
DEMO_HTML="$SCRIPT_DIR/demo/index.html"

echo "┌─────────────────────────────────────────────────┐"
echo "│  OmniVoice Demo Launcher                        │"
echo "└─────────────────────────────────────────────────┘"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌  python3 not found. Please install Python 3."
  exit 1
fi

# Kill any existing server on port 8000
if lsof -ti:8000 &>/dev/null 2>&1; then
  echo "⚠️  Port 8000 in use — stopping existing process..."
  kill "$(lsof -ti:8000)" 2>/dev/null || true
  sleep 0.5
fi

echo "🚀  Starting mock server on ws://localhost:8000/ws/voice ..."
python3 "$MOCK_SERVER" &
SERVER_PID=$!
sleep 0.3

echo "🌐  Opening demo in browser..."
if command -v open &>/dev/null; then
  open "$DEMO_HTML"           # macOS
elif command -v xdg-open &>/dev/null; then
  xdg-open "$DEMO_HTML"       # Linux
elif command -v start &>/dev/null; then
  start "$DEMO_HTML"          # Windows (Git Bash)
else
  echo "   Please open manually:  $DEMO_HTML"
fi

echo ""
echo "✅  Server PID: $SERVER_PID"
echo "   Press Ctrl+C to stop."
echo ""

# Forward server output and wait
wait $SERVER_PID
