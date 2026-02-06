#!/bin/bash
set -e

echo "=== Running bash-operations Example ==="
WORKSPACE=./workspaces/bash-test

# Step 1: Setup
echo "Step 1: Setting up task bash-001..."
python benchmarks/bash-operations/setup.py --workspace-dir $WORKSPACE --task bash-001

# Step 2: Run agent
echo "Step 2: Running mini-swe-agent..."
cd $WORKSPACE && \
  mini --exit-immediately -y \
    -t "Create a file named 'hello.txt' containing the text 'Hello World'"
cd - > /dev/null

# Step 3: Evaluate
echo "Step 3: Evaluating results..."
python benchmarks/bash-operations/evaluate.py --workspace-dir $WORKSPACE --task bash-001

echo ""
echo "View results: ls $WORKSPACE"
echo "Clean up: rm -rf $WORKSPACE"
