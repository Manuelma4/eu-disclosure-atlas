#!/usr/bin/env bash
# Platform CLIs (helm, k3d, kubeconform, tofu, kubectl): the host binary when installed, otherwise its official
# container image with the repository mounted at /repo. Source this file, then call `helm ...`, `k3d ...`.
set -euo pipefail

ATLAS_REPO_ROOT="${ATLAS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HELM_IMAGE="${HELM_IMAGE:-alpine/helm:4.3.1}"
K3D_IMAGE="${K3D_IMAGE:-ghcr.io/k3d-io/k3d:5.9.0}"
KUBECONFORM_IMAGE="${KUBECONFORM_IMAGE:-ghcr.io/yannh/kubeconform:v0.8.0}"
TOFU_IMAGE="${TOFU_IMAGE:-ghcr.io/opentofu/opentofu:1.13.1-minimal}"
KUBECTL_IMAGE="${KUBECTL_IMAGE:-registry.k8s.io/kubectl:v1.36.5}"
# Kubeconfig used by the containerised helm and kubectl (k3d writes one that reaches the API from its network).
ATLAS_KUBECONFIG="${ATLAS_KUBECONFIG:-$ATLAS_REPO_ROOT/.k3d/kubeconfig}"
ATLAS_DOCKER_NETWORK="${ATLAS_DOCKER_NETWORK:-k3d-atlas}"

_workdir() {
  # The current directory inside the container, when it is inside the repository.
  case "$PWD/" in
    "$ATLAS_REPO_ROOT"/*) echo "/repo${PWD#"$ATLAS_REPO_ROOT"}" ;;
    *) echo /repo ;;
  esac
}

_run() {
  local image=$1; shift
  local net=()
  if docker network inspect "$ATLAS_DOCKER_NETWORK" >/dev/null 2>&1; then net=(--network "$ATLAS_DOCKER_NETWORK"); fi
  local kube=()
  if [[ -f "$ATLAS_KUBECONFIG" ]]; then kube=(-v "$ATLAS_KUBECONFIG:/kube/config:ro" -e KUBECONFIG=/kube/config); fi
  docker run --rm -i ${net[@]+"${net[@]}"} ${kube[@]+"${kube[@]}"} -u "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$ATLAS_REPO_ROOT:/repo" -w "$(_workdir)" "$image" "$@"
}

_has() { [[ "${ATLAS_FORCE_CONTAINERS:-0}" != 1 ]] && type -P "$1" >/dev/null 2>&1; }

helm() { if _has helm; then command helm "$@"; else _run "$HELM_IMAGE" "$@"; fi; }
kubeconform() { if _has kubeconform; then command kubeconform "$@"; else _run "$KUBECONFORM_IMAGE" "$@"; fi; }
tofu() { if _has tofu; then command tofu "$@"; else _run "$TOFU_IMAGE" "$@"; fi; }

kubectl() {
  if _has kubectl && [[ "${ATLAS_HOST_KUBECONFIG:-}" != "" ]]; then
    KUBECONFIG="$ATLAS_HOST_KUBECONFIG" command kubectl "$@"
  else
    _run "$KUBECTL_IMAGE" "$@"
  fi
}

k3d() {
  if _has k3d; then command k3d "$@"; return; fi
  # k3d drives the host's Docker daemon; paths in the config are host paths.
  docker run --rm -i -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$ATLAS_REPO_ROOT:$ATLAS_REPO_ROOT" -w "$PWD" "$K3D_IMAGE" "$@"
}
