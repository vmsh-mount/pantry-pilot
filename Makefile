# ─────────────────────────────────────────────────────────────────────────────
# PantryPilot — developer Makefile
# Run from the repo root: make <target>
# ─────────────────────────────────────────────────────────────────────────────

.DEFAULT_GOAL := help
.PHONY: help up down nuke restart logs shell-pilot shell-cockpit \
        migrate migrate-new migrate-down \
        test test-watch lint \
        install-pilot install-cockpit \
        gen-keys clean seed fix-dev

APP_DIR     := app
PILOT_DIR   := $(APP_DIR)/pilot
COCKPIT_DIR := $(APP_DIR)/cockpit

# Docker Compose — support both v2 plugin (docker compose) and legacy standalone
DOCKER_COMPOSE := $(shell docker compose version > /dev/null 2>&1 && echo "docker compose" || echo "$(DOCKER_COMPOSE)")

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  PantryPilot developer commands"
	@echo ""
	@echo "  Stack"
	@echo "    make up              Start all services ($(DOCKER_COMPOSE))"
	@echo "    make down            Stop and remove containers (keeps DB data)"
	@echo "    make nuke            Destroy everything incl. volumes + DB data"
	@echo "    make restart         Restart pilot + cockpit + worker (DB/Redis untouched)"
	@echo "    make logs            Tail all service logs"
	@echo "    make logs s=pilot    Tail a specific service"
	@echo ""
	@echo "  Database"
	@echo "    make migrate         Apply all pending Alembic migrations"
	@echo "    make migrate-new m=<desc>  Create a new migration file"
	@echo "    make migrate-down    Rollback one migration"
	@echo ""
	@echo "  Testing"
	@echo "    make test            Run full pytest suite"
	@echo "    make test f=<file>   Run a single test file"
	@echo "    make test-watch      Re-run tests on file change (requires ptw)"
	@echo ""
	@echo "  Dev setup"
	@echo "    make install-pilot   pip install -r requirements.txt"
	@echo "    make install-cockpit npm install"
	@echo "    make seed            Seed 22 pantry items for the latest household"
	@echo "    make fix-dev         Mark latest household onboarding_complete=true"
	@echo "    make gen-keys        Print fresh TOKEN_ENCRYPTION_KEY + JWT_SECRET"
	@echo "    make lint            Run ruff + mypy on pilot"
	@echo "    make clean           Remove __pycache__, .next, build artefacts"
	@echo ""

# ── Stack ─────────────────────────────────────────────────────────────────────

up:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) up --build -d
	@echo ""
	@echo "  Stack is up"
	@echo "  Cockpit  -> http://localhost:3000"
	@echo "  Pilot    -> http://localhost:8000"
	@echo "  API docs -> http://localhost:8000/docs"
	@echo ""

down:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) down

nuke:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) down -v --remove-orphans
	@echo ""
	@echo "  💥  Everything destroyed (containers + volumes + DB)"
	@echo "  Run 'make up' to start fresh."
	@echo ""

restart:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) up --build -d pilot cockpit pilot-worker pilot-beat
	@echo ""
	@echo "  Restarted pilot + cockpit + worker (DB/Redis untouched)"
	@echo ""

logs:
ifdef s
	cd $(APP_DIR) && $(DOCKER_COMPOSE) logs -f $(s)
else
	cd $(APP_DIR) && $(DOCKER_COMPOSE) logs -f
endif

seed:
	@HH=$$(cd $(APP_DIR) && $(DOCKER_COMPOSE) exec postgres psql -U pantrypilot -d pantrypilot -tAc "SELECT id FROM households ORDER BY created_at DESC LIMIT 1"); \
	if [ -z "$$HH" ]; then echo "No household found — complete onboarding first."; exit 1; fi; \
	echo "Seeding pantry for household $$HH…"; \
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec postgres psql -U pantrypilot -d pantrypilot -c " \
	INSERT INTO pantry_items (id,household_id,item_name,category,standard_unit,estimated_qty_remaining,reorder_threshold,created_at) VALUES \
	  (gen_random_uuid(),'$$HH','Basmati Rice','staples','kg',0.5,1.0,now()), \
	  (gen_random_uuid(),'$$HH','Toor Dal','staples','kg',0.2,0.5,now()), \
	  (gen_random_uuid(),'$$HH','Wheat Atta','staples','kg',1.0,2.0,now()), \
	  (gen_random_uuid(),'$$HH','Amul Butter','dairy','g',50,100,now()), \
	  (gen_random_uuid(),'$$HH','Amul Milk','dairy','L',0.5,1.0,now()), \
	  (gen_random_uuid(),'$$HH','Curd','dairy','g',100,200,now()), \
	  (gen_random_uuid(),'$$HH','Tomatoes','vegetables','kg',0.3,0.5,now()), \
	  (gen_random_uuid(),'$$HH','Onions','vegetables','kg',0.5,1.0,now()), \
	  (gen_random_uuid(),'$$HH','Potatoes','vegetables','kg',0.3,0.5,now()), \
	  (gen_random_uuid(),'$$HH','Turmeric Powder','spices','g',20,50,now()), \
	  (gen_random_uuid(),'$$HH','Cumin Seeds','spices','g',30,50,now()), \
	  (gen_random_uuid(),'$$HH','Sunflower Oil','staples','L',0.3,0.5,now()), \
	  (gen_random_uuid(),'$$HH','Salt','spices','g',100,200,now()), \
	  (gen_random_uuid(),'$$HH','Sugar','staples','g',200,500,now()), \
	  (gen_random_uuid(),'$$HH','Tea','beverages','g',50,100,now()), \
	  (gen_random_uuid(),'$$HH','Biscuits','snacks','g',0,100,now()), \
	  (gen_random_uuid(),'$$HH','Chana Dal','staples','kg',0.1,0.5,now()), \
	  (gen_random_uuid(),'$$HH','Paneer','dairy','g',0,100,now()), \
	  (gen_random_uuid(),'$$HH','Detergent Powder','cleaning','g',100,200,now()), \
	  (gen_random_uuid(),'$$HH','Dish Wash Bar','cleaning','units',1,2,now()), \
	  (gen_random_uuid(),'$$HH','Toilet Soap','personal','units',1,2,now()), \
	  (gen_random_uuid(),'$$HH','Mustard Oil','staples','L',0.2,0.5,now()) \
	ON CONFLICT DO NOTHING;" && echo "✅ Pantry seeded (22 items)"

fix-dev:
	@HH=$$(cd $(APP_DIR) && $(DOCKER_COMPOSE) exec postgres psql -U pantrypilot -d pantrypilot -tAc "SELECT id FROM households ORDER BY created_at DESC LIMIT 1"); \
	if [ -z "$$HH" ]; then echo "No household found."; exit 1; fi; \
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec postgres psql -U pantrypilot -d pantrypilot -c \
	  "UPDATE households SET onboarding_complete = true WHERE id = '$$HH';" && \
	echo "✅ Household $$HH marked onboarding_complete"

shell-pilot:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot bash

shell-cockpit:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec cockpit sh

# ── Database migrations ───────────────────────────────────────────────────────

migrate:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot alembic upgrade head

migrate-new:
ifndef m
	$(error Usage: make migrate-new m="your migration description")
endif
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot alembic revision --autogenerate -m "$(m)"

migrate-down:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot alembic downgrade -1

migrate-history:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot alembic history --verbose

# ── Tests ─────────────────────────────────────────────────────────────────────

test:
ifdef f
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot python3 -m pytest $(f) -v
else
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot python3 -m pytest
endif

test-watch:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot python3 -m pytest_watch -- -q

# ── Code quality ──────────────────────────────────────────────────────────────

lint:
	cd $(APP_DIR) && $(DOCKER_COMPOSE) exec pilot python3 -m ruff check app/ tests/ && $(DOCKER_COMPOSE) exec pilot python3 -m mypy app/ --ignore-missing-imports

# ── Dev setup ─────────────────────────────────────────────────────────────────

install-pilot:
	cd $(PILOT_DIR) && $(PYTHON) -m pip install -r requirements.txt

install-cockpit:
	cd $(COCKPIT_DIR) && npm install

gen-keys:
	@python3 -c "\
import secrets; \
print('TOKEN_ENCRYPTION_KEY=' + secrets.token_hex(32)); \
print('JWT_SECRET=' + secrets.token_hex(32)); \
print('INTERNAL_API_SECRET=' + secrets.token_hex(16)) \
"

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	find $(PILOT_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find $(PILOT_DIR) -type d -name .mypy_cache  -exec rm -rf {} + 2>/dev/null || true
	find $(PILOT_DIR) -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(COCKPIT_DIR)/.next 2>/dev/null || true
	@echo "Clean ✅"
