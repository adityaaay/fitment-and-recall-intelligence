.PHONY: install sample run test lint dashboard docs clean

install:        ## editable install with app, agent and dev extras
	pip install -e ".[app,agent,dev]"

sample:         ## build the warehouse from bundled fixtures (no network, ~1 min)
	fitment run --sample

run:            ## full refresh from NHTSA (downloads ~100 MB, ~750 vPIC calls on first run)
	fitment run

test:           ## unit + integration tests
	pytest

lint:
	ruff check src tests app scripts

dashboard:
	fitment dashboard

docs:           ## dbt docs site with lineage graph
	cd dbt && dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir .

clean:
	rm -rf data dbt/target dbt/logs
