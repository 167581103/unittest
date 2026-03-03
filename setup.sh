#!/bin/bash

# Setup script for unittest project
# This script downloads and configures Jacoco

set -e

echo "Setting up unittest project..."

# Create lib directory if it doesn't exist
mkdir -p lib

# Check if jacoco already exists
if [ -d "lib/jacoco-0.8.14" ]; then
    echo "Jacoco 0.8.14 already exists in lib/ directory"
else
    echo "Downloading Jacoco 0.8.14..."
    cd lib
    wget -q https://github.com/jacoco/jacoco/releases/download/v0.8.14/jacoco-0.8.14.zip
    unzip -q jacoco-0.8.14.zip
    rm jacoco-0.8.14.zip
    cd ..
    echo "Jacoco downloaded successfully"
fi

echo "Setup complete! You can now run evaluation/run.sh"
echo ""
echo "Usage:"
echo "  ./evaluation/run.sh"
