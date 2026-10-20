#!/usr/bin/env bash
# tofu fmt and validate for every module and environment, without a backend or credentials (nothing is applied).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/tools.sh"
cd "$ATLAS_REPO_ROOT/infra/tofu"
tofu fmt -check -recursive -diff
for dir in modules/* environments/*; do
  echo "== $dir"
  (cd "$dir" && tofu init -backend=false -input=false >/dev/null && tofu validate -no-color)
done
