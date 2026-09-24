#!/usr/bin/env bash
# Delegate to the shared evidence, isolation, and ownership implementation.
set -euo pipefail
export TZ=UTC
CONFIG_DIR="${GARDENER_CONFIG_DIR:-$HOME/.config/gardener}"
set -a
[[ ! -f "$CONFIG_DIR/config" ]] || source "$CONFIG_DIR/config"
set +a
exec "$(dirname "$(readlink -f "$0")")/workflow" docsmith "$@"
