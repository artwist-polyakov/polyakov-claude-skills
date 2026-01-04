#!/bin/bash

# SSH Connection Script for FLAB Conversational Server
# Usage:
#   ./connect.sh              - Interactive shell
#   ./connect.sh "command"    - Run command and exit

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/../config"
ENV_FILE="$CONFIG_DIR/.env"

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found"
    echo "Copy .env.example to .env and fill in values"
    exit 1
fi

# Load environment variables
source "$ENV_FILE"

# Validate required variables
if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ] || [ -z "$SSH_KEY_PATH" ]; then
    echo "Error: Missing required variables in .env"
    echo "Required: SSH_HOST, SSH_USER, SSH_KEY_PATH"
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

# Change to project directory on server
CD_CMD="cd $SERVER_PROJECT_PATH &&"

if [ -n "$1" ]; then
    # Run provided command
    ssh "$SSH_USER@$SSH_HOST" "$CD_CMD $*"
else
    # Interactive shell
    ssh -t "$SSH_USER@$SSH_HOST" "$CD_CMD exec \$SHELL -l"
fi
