#!/usr/bin/env bash
# Cloud Agent start phase for MPC Autofill.
# Brings up the database services the backend needs and prepares its schema.
# Must tolerate repeated runs: it starts idempotently and returns once ready.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() {
	echo "[start] $*"
}

start_docker() {
	# Elasticsearch's memory-mapped indices need a raised map count.
	sudo sysctl -w vm.max_map_count=262144 >/dev/null
	if ! sudo docker info >/dev/null 2>&1; then
		log "Starting Docker daemon"
		sudo service docker start
	fi
	until sudo docker info >/dev/null 2>&1; do
		sleep 1
	done
	# Let this session's processes (dev servers, testcontainers) reach the daemon.
	sudo chmod 666 /var/run/docker.sock
}

start_databases() {
	log "Starting Postgres and Elasticsearch"
	(cd "$REPO_ROOT/docker" && docker compose up -d)

	log "Waiting for Postgres"
	until docker exec mpcautofill_postgres pg_isready -U mpcautofill >/dev/null 2>&1; do
		sleep 2
	done

	log "Waiting for Elasticsearch"
	until curl --silent --output /dev/null http://localhost:9200/_cat/health; do
		sleep 2
	done
}

prepare_backend() {
	cd "$REPO_ROOT/MPCAutofill"
	log "Applying database migrations"
	./venv/bin/python manage.py migrate --noinput
	log "Building the Elasticsearch search index"
	./venv/bin/python manage.py search_index --rebuild -f
	cd "$REPO_ROOT"
}

start_docker
start_databases
prepare_backend

log "Start complete"
