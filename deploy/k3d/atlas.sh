#!/usr/bin/env bash
# Local Kubernetes for the Atlas on k3d. Usage: deploy/k3d/atlas.sh <command>
#   up         create the cluster, build and import the images, install the operators and the dev release
#   smoke      create the cluster, build and import the images, install the CI release, check /v1/health
#   create     create the cluster only           images     build the images and import them as tag `dev`
#   operators  install the operators + policies   install    install or upgrade the umbrella chart (dev values)
#   argocd     install Argo CD and the dev app-of-apps (needs repository access, see docs/operations.md)
#   down       delete the cluster
# helm, k3d and kubectl run from their container images when they are not installed (deploy/scripts/tools.sh).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../scripts/tools.sh"
cd "$ATLAS_REPO_ROOT"

CLUSTER=atlas
NAMESPACE="${ATLAS_NAMESPACE:-atlas}"
REGISTRY=ghcr.io/manuelma4
TAG=dev
PUBLIC_API_URL="${ATLAS_PUBLIC_API_URL:-http://api.atlas.localhost:8080}"
mkdir -p .k3d
# Always this cluster, whatever the caller's KUBECONFIG points at.
export KUBECONFIG="$ATLAS_REPO_ROOT/.k3d/kubeconfig-host"

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }

create() {
  if k3d cluster list 2>/dev/null | grep -q "^$CLUSTER "; then
    log "cluster $CLUSTER exists"
  else
    log "creating cluster $CLUSTER"
    k3d cluster create --config deploy/k3d/cluster.yaml
  fi
  # Two kubeconfigs: one for host binaries (127.0.0.1:6550), one for containers on the k3d network.
  k3d kubeconfig get "$CLUSTER" > .k3d/kubeconfig-host
  sed -E 's#server: https://[^:]+:[0-9]+#server: https://k3d-atlas-serverlb:6443#' .k3d/kubeconfig-host > .k3d/kubeconfig
  chmod 600 .k3d/kubeconfig .k3d/kubeconfig-host
  kubectl wait --for=condition=Ready nodes --all --timeout=120s >/dev/null
}

images() {
  log "building images (tag $TAG)"
  docker build -q -t "$REGISTRY/atlas-python:$TAG" . >/dev/null
  docker build -q -t "$REGISTRY/atlas-web:$TAG" --build-arg "NEXT_PUBLIC_ATLAS_API_URL=$PUBLIC_API_URL" web >/dev/null
  local extra=()
  for svc in realtime-gateway alerts; do
    if [[ -f "services/$svc/Dockerfile" ]]; then
      docker build -q -t "$REGISTRY/atlas-$svc:$TAG" "services/$svc" >/dev/null && extra+=("$REGISTRY/atlas-$svc:$TAG")
    fi
  done
  log "importing images into $CLUSTER"
  k3d image import -c "$CLUSTER" "$REGISTRY/atlas-python:$TAG" "$REGISTRY/atlas-web:$TAG" ${extra[@]+"${extra[@]}"}
}

operators() {
  # fd 3: the containerised helm reads stdin.
  while read -r release namespace repo chart version <&3; do
    [[ -z "$release" || "$release" == \#* ]] && continue
    log "operator $release $version"
    helm upgrade --install "$release" "$chart" --repo "$repo" --version "$version" \
      --namespace "$namespace" --create-namespace -f "deploy/platform/values/$release.yaml" \
      --wait --timeout 10m >/dev/null
  done 3< deploy/platform/operators.env
  log "Kyverno policies (signatures audited locally)"
  helm upgrade --install atlas-policies deploy/helm/atlas-policies -n kyverno \
    -f deploy/helm/atlas-policies/values-dev.yaml --wait >/dev/null
}

namespace() {
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl label namespace "$NAMESPACE" --overwrite app.kubernetes.io/part-of=atlas \
    pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest >/dev/null
}

install() {
  local values="${1:-dev}"
  namespace
  log "installing atlas ($values values) into $NAMESPACE"
  (cd deploy/helm && for c in query-api web ingestion extraction ai-service realtime-gateway alerts atlas; do
    helm dependency build "$c" >/dev/null; done)
  helm upgrade --install atlas deploy/helm/atlas -n "$NAMESPACE" -f "deploy/helm/atlas/values-$values.yaml" \
    --set "global.image.tag=$TAG" --wait --timeout 10m
}

check() {
  log "checking the API and the website through the ingress"
  local health="" i
  for i in $(seq 1 30); do
    health="$(curl -fsS -H 'Host: api.atlas.localhost' http://127.0.0.1:8080/v1/health 2>/dev/null)" && break
    sleep 2
  done
  [[ -n "$health" ]] || { echo "API health check failed" >&2; kubectl -n "$NAMESPACE" get pods -o wide >&2; exit 1; }
  echo "GET /v1/health -> $health"
  echo "$health" | grep -q '"status":"ok"'
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: atlas.localhost' http://127.0.0.1:8080/)"
  echo "GET / (website, server-side calls to query-api) -> $code"
  [[ "$code" == 200 ]]
  log "smoke test passed"
}

argocd() {
  log "installing Argo CD"
  helm upgrade --install argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.10.5 \
    -n argocd --create-namespace -f deploy/argocd/install/values.yaml --wait --timeout 10m >/dev/null
  kubectl apply -n argocd -f deploy/argocd/projects/atlas.yaml -f deploy/argocd/environments/dev.yaml
}

case "${1:-}" in
  up) create; images; operators; install dev; check ;;
  smoke) create; images; install ci; check ;;
  create) create ;;
  images) images ;;
  operators) operators ;;
  install) install "${2:-dev}" ;;
  check) check ;;
  argocd) argocd ;;
  down) k3d cluster delete "$CLUSTER"; rm -f .k3d/kubeconfig .k3d/kubeconfig-host ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
