.PHONY: install bash-example py-example

install:
	@echo "Installing dependencies..."
	uv sync
	@echo "Dependencies installed."
	@echo "Activate the environment with: source .venv/bin/activate"

bash-example:
	bash examples/run_bash_example.sh

py-example:
	bash examples/run_python_example.sh
