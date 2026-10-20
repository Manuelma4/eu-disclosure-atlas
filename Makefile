# Platform shortcuts (P6). Python, website and Go commands are in CLAUDE.md and README.md.
.PHONY: k3d-up k3d-smoke k3d-down k3d-argocd deploy-validate tofu-check argocd-platform

k3d-up: ## Local cluster with operators and the full dev release
	deploy/k3d/atlas.sh up

k3d-smoke: ## Local cluster with the API and website only, then /v1/health through the ingress
	deploy/k3d/atlas.sh smoke

k3d-argocd: ## Argo CD and the dev app-of-apps on the local cluster
	deploy/k3d/atlas.sh argocd

k3d-down: ## Delete the local cluster
	deploy/k3d/atlas.sh down

deploy-validate: ## helm lint and kubeconform on every rendered chart
	python3 deploy/scripts/argocd_platform.py --check
	deploy/scripts/validate.sh

tofu-check: ## tofu fmt and validate (no backend, nothing applied)
	deploy/scripts/tofu-check.sh

argocd-platform: ## Regenerate the Argo CD operator applications from deploy/platform/operators.env
	python3 deploy/scripts/argocd_platform.py
