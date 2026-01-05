#!/bin/bash

# SSH Connection Script for Remote Servers
# Usage:
#   ./connect.sh              - Interactive shell
#   ./connect.sh "command"    - Run command and exit
#
# Configuration:
#   - Claude Code (local): use config/.env file
#   - Cloud Runtime: set environment variables directly

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/../config"
ENV_FILE="$CONFIG_DIR/.env"

# Load .env file if exists and variables not already set (local mode)
if [ -f "$ENV_FILE" ] && [ -z "$SSH_HOST" ]; then
    source "$ENV_FILE"
fi

# Validate required variables
if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ] || [ -z "$SSH_KEY_PATH" ]; then
    echo "Error: Missing required variables"
    echo "Required: SSH_HOST, SSH_USER, SSH_KEY_PATH"
    echo ""
    echo "For Claude Code: copy config/.env.example to config/.env"
    echo "For Cloud Runtime: set environment variables"
    exit 1
fi

# Function to add key to ssh-agent if not already added
add_key_to_agent() {
    # Check if key is already in agent
    if ssh-add -l 2>/dev/null | grep -q "$SSH_KEY_PATH"; then
        return 0
    fi

    # Try to add key
    if [ -n "$SSH_KEY_PASSWORD" ]; then
        # Use expect to add key with password
        expect -c "
            spawn ssh-add $SSH_KEY_PATH
            expect \"Enter passphrase\"
            send \"$SSH_KEY_PASSWORD\r\"
            expect eof
        " > /dev/null 2>&1
    else
        # Try without password (will prompt if needed)
        ssh-add "$SSH_KEY_PATH" 2>/dev/null
    fi
}

# Start ssh-agent if not running
if [ -z "$SSH_AUTH_SOCK" ]; then
    eval "$(ssh-agent -s)" > /dev/null 2>&1
fi

# Add key to agent
add_key_to_agent

# Build command prefix (cd to project dir if specified)
if [ -n "$SERVER_PROJECT_PATH" ]; then
    CD_CMD="cd $SERVER_PROJECT_PATH &&"
else
    CD_CMD=""
fi

if [ -n "$1" ]; then
    # Run provided command
    ssh "$SSH_USER@$SSH_HOST" "$CD_CMD $*"
else
    # Interactive shell
    if [ -n "$CD_CMD" ]; then
        ssh -t "$SSH_USER@$SSH_HOST" "$CD_CMD exec \$SHELL -l"
    else
        ssh -t "$SSH_USER@$SSH_HOST"
    fi
fi
