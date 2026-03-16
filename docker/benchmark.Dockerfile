FROM python:3.11-slim

# Pre-install pytest so python-tasks evaluate doesn't need to pip-install at runtime.
# bash-operations only uses stdlib, so this is a no-op for that benchmark.
RUN pip install --no-cache-dir "pytest>=7.0.0"

# Benchmark scripts and workspace are always bind-mounted at runtime — nothing is
# baked into the image, so it never needs to be rebuilt when benchmark logic changes.
WORKDIR /workspace
