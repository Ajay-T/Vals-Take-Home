#!/bin/bash
set -e

echo "=== Running python-tasks Example ==="
WORKSPACE=./workspaces/python-test

# Ensure pytest is available and pip command exists for local testing
uv pip install pytest pip -q

# Step 1: Setup
echo "Step 1: Setting up task py-001..."
python benchmarks/python-tasks/prepare.py py-001 --workspace $WORKSPACE

# Step 2: Run agent
echo "Step 2: Running mini-swe-agent..."
cd $WORKSPACE && \
  mini --exit-immediately -y \
    -t "Write a Python function called add() that returns 4 (2+2)"
cd - > /dev/null

# Step 3: Evaluate
echo "Step 3: Evaluating results..."
python benchmarks/python-tasks/grade.py py-001 --workspace $WORKSPACE

echo ""
echo "View results: ls $WORKSPACE"
echo "Clean up: rm -rf $WORKSPACE"
