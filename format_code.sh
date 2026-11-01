#!/bin/bash

echo "🧹 Starting code formatting..."

# Define target folders and files to format
TARGETS="src/ scripts/ main.py"

echo "📁 Formatting targets: $TARGETS"
echo ""

# Remove unused imports and variables (non-intrusive yet effective)
echo "1️⃣ Removing unused imports and variables..."
autoflake -r --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports $TARGETS

# Fix docstring punctuation, capitalization, and formatting
echo "2️⃣ Formatting docstrings..."
docformatter -r -i --wrap-summaries=88 --wrap-descriptions=88 --make-summary-multi-line $TARGETS

# Automatically sort imports
echo "3️⃣ Sorting import statements..."
isort $TARGETS

# Automatically format code (handles long lines, function arguments, f-strings, etc.)
echo "4️⃣ Formatting code..."
black $TARGETS

# Final static check (non-fixing)
echo "5️⃣ Running static code check..."
flake8 $TARGETS

echo ""
echo "✅ Code formatting completed!"
echo "📊 Processed targets: $TARGETS"
