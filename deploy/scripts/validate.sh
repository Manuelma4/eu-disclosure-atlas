#!/usr/bin/env bash
# helm lint every chart, then kubeconform (strict, Kubernetes 1.36) on the rendered manifests, with CRD schemas
# for the operators (Strimzi, CloudNativePG, KEDA, Kyverno, External Secrets, cert-manager, Argo CD).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/tools.sh"
cd "$ATLAS_REPO_ROOT"
K8S_VERSION="${K8S_VERSION:-1.36.0}"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

deploy/scripts/render.sh "$ATLAS_REPO_ROOT/deploy/.rendered"

(
  cd deploy/helm
  for chart in query-api web ingestion extraction ai-service realtime-gateway alerts atlas-policies; do
    helm lint --strict "$chart" >/dev/null
  done
  for env in dev ci preview prod; do
    helm lint --strict atlas -f "atlas/values-$env.yaml" >/dev/null
  done
  echo "helm lint: ok"
)

kubeconform -strict -summary -kubernetes-version "$K8S_VERSION" \
  -schema-location default -schema-location "$CRD_SCHEMAS" \
  deploy/.rendered deploy/argocd deploy/k3d/manifests
