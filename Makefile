.PHONY: init install docker-build bash-example py-example

init:
	@echo "Initializing git submodules..."
	git submodule update --init --recursive

install:
	@echo "Installing dependencies..."
	uv sync
	@echo "Dependencies installed."
	@echo "Activate the environment with: source .venv/bin/activate"

docker-build:
	@echo "Building benchmark Docker image..."
	docker build -f docker/benchmark.Dockerfile -t harness-benchmark:latest .

bash-example:
	bash examples/run_bash_example.sh

py-example:
	bash examples/run_python_example.sh
