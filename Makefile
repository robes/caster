# Convenience targets. Thin wrappers over the underlying tools — see README and
# docs/HOWTO.md for what each does. Run `make <target>`.
.PHONY: setup test lint pipeline package check closed-loop

setup:          ## install deps + pre-commit hooks
	uv sync --extra dev --extra croissant --extra packaging
	uv run pre-commit install

test:           ## run the caster test suite
	uv run pytest

lint:           ## lint + format check
	uv run ruff check src scripts
	uv run ruff format --check src scripts

pipeline:       ## run the DVC pipeline end to end
	uv run dvc repro

package:        ## build a release bundle (CONFIG=configs/release/<file>.yaml)
	uv run caster package $(CONFIG)

check: test lint  ## the post-change health check

closed-loop:   ## full produce→reproduce round trip (slow; needs network)
	bash tests/closed_loop.sh
