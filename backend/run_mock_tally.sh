#!/bin/bash
# Run Mock Tally Server
# This script starts the mock Tally server on port 9000

echo "Starting Mock Tally Server..."
echo "Server will be available at http://localhost:9000"
echo ""

cd "$(dirname "$0")"
python -m mock_tally
