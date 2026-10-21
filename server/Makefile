.PHONY: up local-up local-recover down clean logs seed wait-api wait-dashboard bootstrap health build run_local reset-admin-password prune-logs test-quick-loopback

OUTPUT ?= text
API_URL ?= http://localhost:8888
DASHBOARD_URL ?= http://localhost:3000

up:
	@for port in 3000 8888; do \
		if lsof -iTCP:$$port -sTCP:LISTEN >/dev/null 2>&1; then \
			echo "error: port $$port is already in use."; \
			echo "find the owning process with: lsof -iTCP:$$port -sTCP:LISTEN"; \
			exit 1; \
		fi; \
	done
	docker compose up -d --build
	@$(MAKE) --no-print-directory wait-api
	@$(MAKE) --no-print-directory wait-dashboard
	@echo ""
	@echo "Stack is ready."
	@echo "  Dashboard:  $(DASHBOARD_URL)  <- open this to run the setup wizard"
	@echo "  API:        $(API_URL)"

local-up:
	docker compose --profile local-memory up -d
	@$(MAKE) --no-print-directory wait-api
	@$(MAKE) --no-print-directory wait-dashboard
	@echo "Local memory stack is ready (API, dashboard, Postgres, Qdrant, and Neo4j)."

local-recover:
	@echo "Recreating the local stack and Compose network; named data volumes are preserved."
	docker compose --profile local-memory down --remove-orphans
	@$(MAKE) --no-print-directory local-up

down:
	docker compose down

clean:
	docker compose down -v

logs:
	docker compose logs -f

seed:
	@API_URL="$(API_URL)" DASHBOARD_URL="$(DASHBOARD_URL)" EMAIL="$(EMAIL)" PASSWORD="$(PASSWORD)" NAME="$(NAME)" OUTPUT="$(OUTPUT)" ./scripts/seed.sh

wait-api:
	@echo "Waiting for API..."
	@until curl -fsS "$(API_URL)/auth/setup-status" >/dev/null 2>&1; do sleep 2; done

wait-dashboard:
	@echo "Waiting for dashboard..."
	@until curl -fsS "$(DASHBOARD_URL)/api/health" >/dev/null 2>&1; do sleep 2; done

bootstrap: up wait-api wait-dashboard seed

health:
	@echo "API:       $$(curl -s -o /dev/null -w '%{http_code}' "$(API_URL)/docs")"
	@echo "Dashboard: $$(curl -s -o /dev/null -w '%{http_code}' "$(DASHBOARD_URL)/api/health")"
	@echo "Postgres:  $$(docker compose exec -T postgres pg_isready -q && echo 'ok' || echo 'down')"

reset-admin-password:
	@test -n "$(EMAIL)" || (echo "usage: make reset-admin-password EMAIL=<email> PASSWORD=<password>" && exit 2)
	@test -n "$(PASSWORD)" || (echo "usage: make reset-admin-password EMAIL=<email> PASSWORD=<password>" && exit 2)
	@docker compose exec -T -e EMAIL="$(EMAIL)" -e PASSWORD="$(PASSWORD)" -e PYTHONPATH=/app mem0 python scripts/reset_admin_password.py

prune-logs:
	@docker compose exec -T -e REQUEST_LOG_RETENTION_DAYS="$(REQUEST_LOG_RETENTION_DAYS)" -e PYTHONPATH=/app mem0 python scripts/prune_request_logs.py

build:
	docker build -t mem0-api-server .

run_local:
	docker run -p 8000:8000 -v $(shell pwd):/app mem0-api-server --env-file .env

QUICK_LOOPBACK_BASE_IMAGE ?= mem0-server-quick-loopback-base
QUICK_LOOPBACK_IMAGE ?= mem0-server-quick-loopback-test

test-quick-loopback:
	@test -n "$(QUICK_LOOPBACK_ARTIFACTS)" || (echo "QUICK_LOOPBACK_ARTIFACTS must be an absolute directory" && exit 2)
	@case "$(QUICK_LOOPBACK_ARTIFACTS)" in /*) ;; *) echo "QUICK_LOOPBACK_ARTIFACTS must be an absolute directory" && exit 2 ;; esac
	@mkdir -p "$(QUICK_LOOPBACK_ARTIFACTS)"
	docker build --target dependencies -f dev.Dockerfile -t "$(QUICK_LOOPBACK_BASE_IMAGE)" ..
	docker build --build-arg BASE_IMAGE="$(QUICK_LOOPBACK_BASE_IMAGE)" -f quick-loopback-test.Dockerfile -t "$(QUICK_LOOPBACK_IMAGE)" ..
	docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
		--workdir /workspace/server \
		--mount type=bind,src="$(QUICK_LOOPBACK_ARTIFACTS)",dst=/artifacts \
		-e HOME=/tmp/home -e XDG_CONFIG_HOME=/tmp/home/.config -e XDG_CACHE_HOME=/tmp/home/.cache \
		-e PYTHON_DOTENV_DISABLED=true -e AUTH_DISABLED=true -e JWT_SECRET=quick-loopback-test-secret \
		-e MEM0_TELEMETRY=false -e OPENAI_API_KEY= -e ANTHROPIC_API_KEY= -e GOOGLE_API_KEY= \
		"$(QUICK_LOOPBACK_IMAGE)" sh -c 'mkdir -p /tmp/home/.config /tmp/home/.cache /tmp/pytest-cache && exec python -m pytest tests/test_quick_loopback.py $(QUICK_LOOPBACK_PYTEST_ARGS) -q -o cache_dir=/tmp/pytest-cache --junitxml=/artifacts/quick-loopback.xml'
