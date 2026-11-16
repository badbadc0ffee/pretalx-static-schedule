set shell := ["bash", "-euo", "pipefail", "-c"]
set quiet
set fallback
set positional-arguments

uv_dev := "uv run --frozen --extra=dev"

# Run ruff format
[group('linting')]
format *args=".":
    {{ uv_dev }} ruff format "$@"

# Run ruff check
[group('linting')]
check *args=".":
    {{ uv_dev }} ruff check "$@"

# Format Django templates with djangofmt.
[group('linting')]
djangofmt *args="":
    {{ uv_dev }} djangofmt {{ args }} .

[group('linting')]
djangofmt-check:
    {{ uv_dev }} djangofmt --check .

# Check that VCS files and the sdist match (CI runs this)
[group('linting')]
check-manifest:
    {{ uv_dev }} check-manifest --no-build-isolation

# Run all formatters and linters
[group('linting')]
fmt: (check "--fix") format djangofmt && fmt-done

[private]
fmt-done:
    echo '{{ GREEN }}Formatting complete{{ NORMAL }}'

# Run all code quality checks
[group('linting')]
fmt-check: (format "--check") check djangofmt-check && check-done

[private]
check-done:
    echo '{{ GREEN }}All checks passed{{ NORMAL }}'

# Run all tests with coverage.
[group('tests')]
test *args:
    {{ uv_dev }} pytest --cov --cov-branch --cov-fail-under=100 "$@"

# Run the same quality checks as CI.
ci: fmt-check test package-check

# Check the source archive and wheel.
[group('linting')]
package-check: check-manifest
    {{ uv_dev }} python -m build --no-isolation
    {{ uv_dev }} twine check dist/*
