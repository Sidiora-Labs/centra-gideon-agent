.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
VENV ?= .venv/bin
DEV_HOME ?= $(CURDIR)/.dev-home
DEV_PORT ?= 10000

PKG := runtime/gideon
TESTS := checks/runtime
HARNESS := checks/harness
WEB_DIR := apps/console
DESKTOP_DIR := apps/desktop
PYI_BUNDLE_DIR := dist/gideon-backend
BACKEND_RECIPE := tooling/packaging/runtime-bundle.spec
PYTHON_SOURCES := $(PKG) $(TESTS) $(HARNESS)

COMPOSE ?= $(or $(and $(shell command -v docker 2>/dev/null),docker compose),$(and $(shell command -v podman-compose 2>/dev/null),podman-compose),$(and $(shell command -v finch 2>/dev/null),finch compose),docker compose)
COMPOSE_DIR := infrastructure/compose
BASE_FILE := -f $(COMPOSE_DIR)/compose.yaml
BUILD_OVERLAY := $(BASE_FILE) -f $(COMPOSE_DIR)/compose.build.yaml
PROD_OVERLAY := $(BASE_FILE) -f $(COMPOSE_DIR)/compose.prod.yaml
DEV_OVERLAY := $(BUILD_OVERLAY) -f $(COMPOSE_DIR)/compose.dev.yaml

RUN_COMMANDS := serve serve-fresh serve-web
CHECK_COMMANDS := format lint test test-e2e test-visual harness-validate gates
PACKAGE_COMMANDS := build web-build pyinstaller backend-build desktop desktop-dist desktop-dist-linux
CONTAINER_COMMANDS := docker-build docker-up docker-down docker-logs docker-deploy dev-up dev-down
COMMANDS := help $(RUN_COMMANDS) $(CHECK_COMMANDS) $(PACKAGE_COMMANDS) $(CONTAINER_COMMANDS) clean

help.help := Show workspace commands
help.serve := Run the gateway and compiled console
help.serve-fresh := Compile the console before starting the gateway
help.serve-web := Start the console development server
help.format := Format Python runtime and checks
help.lint := Check Python formatting, lint and types
help.test := Run Python tests
help.test-e2e := Run Chromium interaction checks
help.test-visual := Compare Chromium visual snapshots
help.harness-validate := Validate development harness specifications
help.gates := Report all configured maintenance gates
help.build := Package Python source and wheel distributions
help.web-build := Install workspace dependencies and compile the console
help.pyinstaller := Freeze the console and Python runtime
help.backend-build := Build the standalone runtime bundle
help.desktop := Stage the runtime bundle for Electron
help.desktop-dist := Package the desktop application for macOS
help.desktop-dist-linux := Package the Linux AppImage and Debian application
help.docker-build := Build container images from this checkout
help.docker-up := Start the default container stack
help.docker-down := Stop the default container stack
help.docker-logs := Follow container output
help.docker-deploy := Build images and start the production stack
help.dev-up := Build and start containers with live source mounts
help.dev-down := Stop development containers
help.clean := Remove generated packages, console output and caches

.PHONY: $(COMMANDS)

help:
	@printf '%-21s %s\n' $(foreach command,$(COMMANDS),'$(command)' '$(help.$(command))')

serve:
	GIDEON_HOME="$(DEV_HOME)" GIDEON_WORKSPACE="$(DEV_HOME)/workspace" \
		GIDEON_BIND_HOST=0.0.0.0 GIDEON_BYPASS_LOCAL_NETWORKS=1 \
		"$(VENV)/gideon" gateway --no-open --port "$(DEV_PORT)" --json-ready

serve-fresh: web-build
	$(MAKE) serve

serve-web:
	GIDEON_PORT="$(DEV_PORT)" npm --prefix "$(WEB_DIR)" run dev

format:
	$(PYTHON) -m black $(PYTHON_SOURCES)
	$(PYTHON) -m isort $(PYTHON_SOURCES)

lint:
	$(PYTHON) -m black --check $(PYTHON_SOURCES)
	$(PYTHON) -m isort --check-only $(PYTHON_SOURCES)
	$(PYTHON) -m flake8 $(PYTHON_SOURCES)
	$(PYTHON) -m mypy $(PKG) $(HARNESS)

test:
	$(PYTHON) -m pytest

test-e2e:
	cd $(WEB_DIR) && npx playwright test --project=chromium --ignore-snapshots

test-visual:
	cd $(WEB_DIR) && npx playwright test e2e/visual.spec.ts --project=chromium

harness-validate:
	$(PYTHON) -m checks.harness validate

gates:
	$(PYTHON) tooling/scripts/gate_report.py

build:
	$(PYTHON) -m build

web-build:
	npm ci
	npm run build --workspace "$(WEB_DIR)"
	mkdir -p "$(PKG)/static"
	rm -rf "$(PKG)/static/dist"
	ln -s "../../../$(WEB_DIR)/dist" "$(PKG)/static/dist"

pyinstaller: web-build
	"$(VENV)/pyinstaller" "$(BACKEND_RECIPE)" --noconfirm

backend-build: pyinstaller

desktop: pyinstaller
	rm -rf "$(DESKTOP_DIR)/backend-dist"
	mkdir -p "$(DESKTOP_DIR)/backend-dist"
	cp -R "$(PYI_BUNDLE_DIR)" "$(DESKTOP_DIR)/backend-dist/"

desktop-dist: desktop
	npm --prefix "$(DESKTOP_DIR)" run dist

desktop-dist-linux: desktop
	npm --prefix "$(DESKTOP_DIR)" run dist:linux

docker-build:
	$(COMPOSE) $(BUILD_OVERLAY) build

docker-up:
	$(COMPOSE) $(BASE_FILE) up -d

docker-down:
	$(COMPOSE) $(BASE_FILE) down

docker-logs:
	$(COMPOSE) $(BASE_FILE) logs -f

docker-deploy: docker-build
	$(COMPOSE) $(PROD_OVERLAY) up -d

dev-up:
	$(COMPOSE) $(DEV_OVERLAY) up -d --build

dev-down:
	$(COMPOSE) $(DEV_OVERLAY) down

clean:
	rm -rf build dist *.egg-info runtime/*.egg-info
	rm -rf .pytest_cache .mypy_cache .hypothesis .coverage htmlcov
	rm -rf "$(PKG)/static/dist" "$(WEB_DIR)/dist"
	rm -rf "$(DESKTOP_DIR)/dist" "$(DESKTOP_DIR)/backend-dist"
	rm -f "$(WEB_DIR)/tsconfig.tsbuildinfo"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name .DS_Store -delete 2>/dev/null || true
