.PHONY: install playground run test lint

# Install all project dependencies (including dev group)
install:
	uv sync --all-groups

# Launch the ADK web playground UI at http://localhost:18081
# Uses the explicit agent directory path to avoid wildcard expansion crash on Windows
playground:
	uv run adk web app --host 127.0.0.1 --port 18081 --reload_agents

# Run the agent as a local A2A HTTP server (port 8090)
run:
	uv run adk api_server app --host 127.0.0.1 --port 8090

# Run the test suite
test:
	uv run pytest tests/ -v

# Lint and format check
lint:
	uv run ruff check app/ tests/
	uv run ruff format --check app/ tests/
