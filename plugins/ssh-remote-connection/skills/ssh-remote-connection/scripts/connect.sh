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

SSH_AUTH_METHOD="${SSH_AUTH_METHOD:-auto}"

# Validate required variables
if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ]; then
    echo "Error: Missing required variables"
    echo "Required: SSH_HOST, SSH_USER"
    echo ""
    echo "For Claude Code: copy config/.env.example to config/.env"
    echo "For Cloud Runtime: set environment variables"
    exit 1
fi

case "$SSH_AUTH_METHOD" in
    auto|"")
        if [ -n "$SSH_KEY_PATH" ] && [ -r "$SSH_KEY_PATH" ]; then
            AUTH_METHOD="key"
        elif [ -n "$SSH_PASSWORD" ]; then
            AUTH_METHOD="password"
        elif [ -n "$SSH_KEY_PATH" ]; then
            AUTH_METHOD="key"
        else
            AUTH_METHOD=""
        fi
        ;;
    key|password)
        AUTH_METHOD="$SSH_AUTH_METHOD"
        ;;
    *)
        echo "Error: Invalid SSH_AUTH_METHOD: $SSH_AUTH_METHOD"
        echo "Allowed values: auto, key, password"
        exit 1
        ;;
esac

if [ "$AUTH_METHOD" = "key" ]; then
    if [ -z "$SSH_KEY_PATH" ]; then
        echo "Error: SSH_KEY_PATH is required for key authentication"
        exit 1
    fi
    if [ ! -r "$SSH_KEY_PATH" ]; then
        echo "Error: SSH key is not readable: $SSH_KEY_PATH"
        exit 1
    fi
elif [ "$AUTH_METHOD" = "password" ]; then
    if [ -z "$SSH_PASSWORD" ]; then
        echo "Error: SSH_PASSWORD is required for password authentication"
        exit 1
    fi
else
    echo "Error: Missing SSH authentication configuration"
    echo "Set SSH_KEY_PATH for key authentication or SSH_PASSWORD for password authentication."
    exit 1
fi

# Function to add key to ssh-agent if not already added
add_key_to_agent() {
    # Check if key is already in agent
    if ssh-add -l 2>/dev/null | grep -Fq "$SSH_KEY_PATH"; then
        return 0
    fi

    # Try to add key
    if [ -n "$SSH_KEY_PASSWORD" ]; then
        # Use expect to add key with password
        if ! command -v expect >/dev/null 2>&1; then
            echo "Error: SSH_KEY_PASSWORD requires expect to add the key non-interactively"
            exit 1
        fi
        EXPECT_SSH_KEY_PATH="$SSH_KEY_PATH" EXPECT_SSH_KEY_PASSWORD="$SSH_KEY_PASSWORD" expect -c '
            spawn ssh-add $env(EXPECT_SSH_KEY_PATH)
            expect "Enter passphrase"
            send -- "$env(EXPECT_SSH_KEY_PASSWORD)\r"
            expect eof
            catch wait result
            exit [lindex $result 3]
        ' > /dev/null 2>&1
    else
        # Try without password (will prompt if needed)
        ssh-add "$SSH_KEY_PATH" 2>/dev/null
    fi
}

run_ssh_with_password() {
    if command -v sshpass >/dev/null 2>&1; then
        SSHPASS="$SSH_PASSWORD" sshpass -e ssh "$@"
        return $?
    fi

    if command -v expect >/dev/null 2>&1; then
        EXPECT_SSH_PASSWORD="$SSH_PASSWORD" EXPECT_INTERACTIVE="${EXPECT_INTERACTIVE:-0}" expect -f - -- "$@" <<'EXPECT'
            set timeout -1
            set password $env(EXPECT_SSH_PASSWORD)
            set interactive $env(EXPECT_INTERACTIVE)
            log_user 0
            spawn ssh {*}$argv
            log_user 1
            expect {
                -nocase -re "are you sure you want to continue connecting.*" {
                    puts stderr "Error: SSH host key is not trusted yet. Connect once manually or add the host to known_hosts."
                    exit 6
                }
                -nocase -re "(password|passcode).*: *$" {
                    send -- "$password\r"
                    if {$interactive == "1"} {
                        interact
                        catch wait result
                        exit [lindex $result 3]
                    }
                    exp_continue
                }
                eof {
                    catch wait result
                    exit [lindex $result 3]
                }
            }
EXPECT
        return $?
    fi

    echo "Error: Password authentication requires sshpass or expect."
    echo "Install sshpass, install expect, or use SSH key authentication."
    return 1
}

run_ssh() {
    if [ "$AUTH_METHOD" = "password" ]; then
        run_ssh_with_password "$@"
    else
        ssh "$@"
    fi
}

SSH_ARGS=()
if [ -n "$SSH_PORT" ]; then
    SSH_ARGS+=("-p" "$SSH_PORT")
fi

if [ "$AUTH_METHOD" = "key" ]; then
    # Start ssh-agent if not running
    if [ -z "$SSH_AUTH_SOCK" ]; then
        eval "$(ssh-agent -s)" > /dev/null 2>&1
    fi

    # Add key to agent for the initial connection and forwarded auth on the server.
    if ! add_key_to_agent; then
        echo "Error: Failed to add SSH key to ssh-agent: $SSH_KEY_PATH"
        exit 1
    fi
    SSH_ARGS+=("-A" "-i" "$SSH_KEY_PATH")
else
    SSH_ARGS+=("-o" "PreferredAuthentications=password,keyboard-interactive" "-o" "PubkeyAuthentication=no")
fi

# Build command prefix (cd to project dir if specified)
if [ -n "$SERVER_PROJECT_PATH" ]; then
    printf -v SERVER_PROJECT_PATH_QUOTED "%q" "$SERVER_PROJECT_PATH"
    CD_CMD="cd $SERVER_PROJECT_PATH_QUOTED &&"
else
    CD_CMD=""
fi

if [ -n "$1" ]; then
    # Run provided command
    run_ssh "${SSH_ARGS[@]}" "$SSH_USER@$SSH_HOST" "$CD_CMD $*"
else
    # Interactive shell
    if [ -n "$CD_CMD" ]; then
        EXPECT_INTERACTIVE=1 run_ssh "${SSH_ARGS[@]}" -t "$SSH_USER@$SSH_HOST" "$CD_CMD exec \$SHELL -l"
    else
        EXPECT_INTERACTIVE=1 run_ssh "${SSH_ARGS[@]}" -t "$SSH_USER@$SSH_HOST"
    fi
fi
