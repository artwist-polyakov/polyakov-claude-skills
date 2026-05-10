#!/usr/bin/env bash

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"

bash "$TEST_DIR/test_config_loader.sh"

echo "telegram-channel-parser tests passed"
