#!/usr/bin/env bash
# Cloud Agent install phase for MPC Autofill.
# Prepares system toolchains and installs dependencies for every subproject.
# Must be idempotent: it runs after each checkout and may run against cached state.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NODE_VERSION="22.15.1"

log() {
	echo "[install] $*"
}

install_system_packages() {
	log "Installing system packages"
	export DEBIAN_FRONTEND=noninteractive
	local apt_opts=(-y -o Dpkg::Options::=--force-confold)

	if ! command -v python3.13 >/dev/null 2>&1; then
		sudo add-apt-repository -y ppa:deadsnakes/ppa
	fi
	sudo apt-get update -y
	sudo apt-get install "${apt_opts[@]}" \
		python3.13 python3.13-venv python3.13-dev \
		fuse-overlayfs curl ca-certificates

	if ! command -v docker >/dev/null 2>&1; then
		log "Installing Docker Engine"
		curl -fsSL https://get.docker.com | sudo sh
	fi
	sudo usermod -aG docker "$(id -un)" || true
}

configure_docker_daemon() {
	# Cloud Agent VMs run nested, where the default overlayfs snapshotter cannot
	# mount. Use the fuse-overlayfs graph driver with the classic image store.
	log "Configuring Docker daemon for nested virtualisation"
	sudo mkdir -p /etc/docker
	echo '{
  "features": { "containerd-snapshotter": false },
  "storage-driver": "fuse-overlayfs"
}' | sudo tee /etc/docker/daemon.json >/dev/null
}

load_node() {
	export NVM_DIR="$HOME/.nvm"
	if [ ! -s "$NVM_DIR/nvm.sh" ]; then
		log "Installing nvm"
		curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
	fi
	# shellcheck disable=SC1091
	. "$NVM_DIR/nvm.sh"
	nvm install "$NODE_VERSION" >/dev/null
	nvm alias default "$NODE_VERSION" >/dev/null
	nvm use "$NODE_VERSION" >/dev/null
	log "Using Node $(node --version)"
}

install_node_projects() {
	for project in schemas frontend image-cdn; do
		log "Installing npm dependencies for $project"
		(cd "$REPO_ROOT/$project" && npm ci)
	done
}

install_python_project() {
	local project="$1"
	log "Installing Python dependencies for $project"
	cd "$REPO_ROOT/$project"
	if [ ! -x venv/bin/python ]; then
		python3.13 -m venv venv
	fi
	./venv/bin/python -m pip install --upgrade pip
	./venv/bin/pip install -r requirements.txt
	cd "$REPO_ROOT"
}

collect_backend_static() {
	# The backend uses ManifestStaticFilesStorage, so the static manifest must
	# exist before Django can import its URL configuration (see MPCAutofill/urls.py).
	log "Collecting backend static files"
	(cd "$REPO_ROOT/MPCAutofill" && ./venv/bin/python manage.py collectstatic --noinput)
}

install_system_packages
configure_docker_daemon
load_node
install_node_projects
install_python_project MPCAutofill
install_python_project desktop-tool
collect_backend_static

log "Install complete"
