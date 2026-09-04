#!/bin/sh
# Check the Yandex Cloud Wordstat API connection.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

load_config

echo "Checking Wordstat API connection..."
echo ""

# Test with a simple regions request
response=$(wordstat_request "regions" '{"phrase":"тест"}')

if echo "$response" | grep -q '"regions"'; then
    echo "Wordstat API: OK"
    echo ""

    # Count regions in response
    region_count=$(echo "$response" | grep -o '"regionId"' | wc -l | tr -d ' ')
    echo "Test query 'тест' returned data for $region_count regions"
else
    echo "Wordstat API: Error"
    echo "$response"
    exit 1
fi

echo ""
print_backend_info
echo ""
echo "Token/credentials are valid and API is accessible."
