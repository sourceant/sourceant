.PHONY: help up down build test lint format logs shell db-upgrade worker

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Docker:"
	@echo "  up           Start all services"
	@echo "  down         Stop all services"
	@echo "  build        Rebuild containers"
	@echo "  logs         View container logs"
	@echo "  shell        Open a shell in the app container"
	@echo ""
	@echo "Development:"
	@echo "  test         Run tests"
	@echo "  lint         Run flake8 linter"
	@echo "  format       Format code with black"
	@echo ""
	@echo "Database:"
	@echo "  db-upgrade   Run database migrations"
	@echo ""
	@echo "Worker:"
	@echo "  worker       Start the RQ worker"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up -d --build

logs:
	docker compose logs -f

shell:
	docker compose exec app bash

test:
	docker compose exec app pytest src/tests/ -v

lint:
	docker compose exec app black --check .

format:
	docker compose exec app black .

db-upgrade:
	docker compose exec app sourceant db upgrade head

worker:
	docker compose exec app rq worker --url redis://redis:6379
