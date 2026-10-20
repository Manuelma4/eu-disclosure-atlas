#!/usr/bin/env bash
# Render every chart for every environment into an output folder (default: deploy/.rendered), for kubeconform
# and for reviewing diffs. Usage: deploy/scripts/render.sh [out-dir]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/tools.sh"
cd "$ATLAS_REPO_ROOT/deploy/helm"
out="${1:-$ATLAS_REPO_ROOT/deploy/.rendered}"
mkdir -p "$out"
[[ "$out" == "$ATLAS_REPO_ROOT"/* ]] || { echo "render.sh: the output folder must be inside the repository" >&2; exit 1; }
# Relative to deploy/helm, so the path also works inside the helm container.
rel_out="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1]))' "$out")"

for chart in query-api web ingestion extraction ai-service realtime-gateway alerts atlas; do
  helm dependency build "$chart" >/dev/null
done

# The umbrella chart per environment, with every optional service switched on so its manifests are checked too.
all_on=(--set extraction.enabled=true,ai-service.enabled=true,realtime-gateway.enabled=true,alerts.enabled=true)
for env in dev ci preview prod; do
  helm template atlas atlas --namespace atlas -f "atlas/values-$env.yaml" "${all_on[@]}" \
    --set global.image.tag=sha-0000000 > "$rel_out/atlas-$env.yaml"
done
helm template atlas-policies atlas-policies > "$rel_out/atlas-policies.yaml"
helm template atlas-policies atlas-policies -f atlas-policies/values-dev.yaml > "$rel_out/atlas-policies-dev.yaml"
echo "rendered into $out"
