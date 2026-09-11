#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo "Script directory: $SCRIPT_DIR" >&2

# Print startup message (directed to stderr so it doesn't interfere with JSON)
echo "Starting Canvas MCP Server..." >&2

# Load environment variables from .env file
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    echo "Loading environment variables from .env file: $ENV_FILE" >&2
    export $(cat "$ENV_FILE" | grep -v '^#' | xargs)
else
    echo "Error: .env file not found at $ENV_FILE. Copy env.template and set CANVAS_SESSION_COOKIE or CANVAS_CHROME_USER_DATA_DIR (see docs/harvard-session-auth.md)." >&2
    exit 1
fi

# Harvard student defaults when unset
if [ -z "$CANVAS_API_URL" ]; then
    export CANVAS_API_URL="https://canvas.harvard.edu/api/v1"
fi
if [ -z "$CANVAS_ROLE" ]; then
    export CANVAS_ROLE="student"
fi

# Session auth is primary; token is optional fallback. Never echo secret values.
if [ -z "$CANVAS_SESSION_COOKIE" ] && [ -z "$CANVAS_CHROME_USER_DATA_DIR" ] && [ -z "$CANVAS_API_TOKEN" ]; then
    echo "Error: set CANVAS_SESSION_COOKIE or CANVAS_CHROME_USER_DATA_DIR (CANVAS_API_TOKEN is optional)" >&2
    exit 1
fi

# Go to the script directory
cd $SCRIPT_DIR
echo "Changed directory to: $(pwd)" >&2

# Run the server using the repo-local venv if present (preferred)
VENV_SERVER="$SCRIPT_DIR/.venv/bin/canvas-mcp-server"
if [ -x "$VENV_SERVER" ]; then
    echo "Starting server with $VENV_SERVER ..." >&2
    "$VENV_SERVER"
else
    echo "Starting server with canvas-mcp-server from PATH..." >&2
    canvas-mcp-server
fi

# Exit message
echo "Server stopped" >&2