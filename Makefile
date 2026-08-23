IMAGE_NAME ?= ghcr.io/sourceant/sourceant
IMAGE_TAG ?= latest

.PHONY: help up down build test lint lint-fix format logs shell db-upgrade worker prod-build prod-push

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
	@echo "  lint         Check code formatting with black"
	@echo "  lint-fix     Fix code formatting with black"
	@echo "  format       Format code with black"
	@echo ""
	@echo "Database:"
	@echo "  db-upgrade   Run database migrations"
	@echo ""
	@echo "Production:"
	@echo "  prod-build   Build production Docker image"
	@echo "  prod-push    Push production Docker image to GHCR"
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

lint-fix:
	docker compose exec app black .

format:
	docker compose exec app black .

db-upgrade:
	docker compose exec app sourceant db upgrade head

prod-build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) -f Dockerfile .

prod-push:
	docker push $(IMAGE_NAME):$(IMAGE_TAG)

worker:
	docker compose exec app rq worker --url redis://redis:6379
