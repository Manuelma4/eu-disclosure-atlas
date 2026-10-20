# Operations: Kubernetes, GitOps and the supply chain

How the Atlas runs on Kubernetes, on a laptop (k3d) and in production (Scaleway Kapsule), how a change reaches
production, how to roll back and how pull-request previews work. Design: [decision 0012](decisions/0012-kubernetes-gitops-and-infrastructure-as-code.md).

## Map

| Path | Contents |
|---|---|
| `Dockerfile` | One Python image (`atlas-python`) for query-api, ingestion and extraction; uv with `uv.lock`, non-root (uid 10001) |
| `web/Dockerfile` | Next.js standalone build on distroless Node.js 24 (`atlas-web`, uid 65532) |
| `deploy/helm/atlas-lib` | Library chart: workload, service, probes, PDB, NetworkPolicy, HPA, KEDA ScaledObject, ingress |
| `deploy/helm/<service>` | One chart per service: query-api, web, ingestion, extraction, ai-service, realtime-gateway, alerts |
| `deploy/helm/atlas` | Umbrella chart: every service, default-deny policy, lake volume, Strimzi Kafka and topics, CloudNativePG, ExternalSecrets; `values-{dev,ci,preview,prod}.yaml` |
| `deploy/helm/atlas-policies` | Kyverno policies: signed images only, no `latest`, required resources |
| `deploy/platform/` | Operator versions (`operators.env`), their values, and cluster-wide manifests (ClusterIssuer, ClusterSecretStore) |
| `deploy/argocd/` | Argo CD install values, projects, one root app per environment, child apps, the preview ApplicationSet |
| `deploy/k3d/` | Local cluster configuration and `atlas.sh` |
| `deploy/scripts/` | Tool shims, render and validation, OpenTofu checks, Argo CD generator |
| `deploy/secrets/` | SOPS rules and the bootstrap secret template |
| `infra/tofu/` | OpenTofu modules (kapsule, object-storage, dns) and environments (prod, preview) |
| `.github/workflows/` | `images.yml` (build, scan, SBOM, sign), `platform.yml` (lint, kubeconform, tofu, k3d smoke), `deploy.yml` (promotion) |

## Every pod

The library chart gives every service the same baseline, so a chart cannot forget it:

- **Pod Security `restricted`:** non-root user, no privilege escalation, all capabilities dropped, `RuntimeDefault`
  seccomp, read-only root filesystem (writable paths are explicit `emptyDir` volumes), no service-account token.
  Atlas namespaces enforce the profile with the `pod-security.kubernetes.io/enforce=restricted` label.
- **Probes** on the HTTP services (startup, liveness, readiness); worker loops get theirs with their HTTP
  endpoints (P2, WS1).
- **Resources:** CPU and memory requests and a memory limit, required by Kyverno.
- **PodDisruptionBudget** for every service that serves traffic.
- **NetworkPolicies:** the namespace is default-deny in both directions; each service allows DNS plus only the
  peers it needs. For example web may call query-api on 8000, query-api accepts web and the ingress
  controller, ingestion may reach the internet on 80/443 (regulators, ECB) and Kafka and PostgreSQL, extraction
  reaches Kafka and the Claude API.
- **Scaling:** HPAs on CPU for query-api and web; KEDA ScaledObjects on Kafka consumer lag for extraction
  (scales to zero) and alerts.

## Running locally on k3d

Needs Docker (8 GB of memory for the full stack, 3 GB for the smoke test). `helm`, `k3d`, `kubectl`,
`kubeconform` and `tofu` are used from the host when installed, otherwise from their official container images
(`deploy/scripts/tools.sh`), so nothing else is required.

```bash
make k3d-up
```

This creates the cluster `atlas` (one server, one agent, k3s 1.36), builds `atlas-python:dev` and `atlas-web:dev`
and imports them, installs the operators at the versions in `deploy/platform/operators.env` (Strimzi,
CloudNativePG, cert-manager, KEDA, Kyverno, External Secrets) and the Kyverno policies (signature checks in
Audit mode, since local images are unsigned), then the umbrella chart with `values-dev.yaml`: query-api, web,
ingestion (live Sweden FI connector), a single-node Kafka 4 with the Atlas topics and a PostgreSQL 18 cluster.

| URL | What |
|---|---|
| http://atlas.localhost:8080 | Website |
| http://api.atlas.localhost:8080/v1/health | API (`/docs` for OpenAPI) |

```bash
export KUBECONFIG="$PWD/.k3d/kubeconfig-host"   # the cluster's kubeconfig; atlas.sh never touches ~/.kube
kubectl -n atlas get pods
deploy/k3d/atlas.sh images && deploy/k3d/atlas.sh install   # rebuild and roll out after a code change
make k3d-down
```

Several clusters can share one Docker daemon: `ATLAS_K3D_CLUSTER` (default `atlas`), `ATLAS_K3D_API_PORT`
(6550), `ATLAS_K3D_HTTP_PORT` (8080) and `ATLAS_K3D_HTTPS_PORT` (8443) choose the cluster name and host ports,
for example `ATLAS_K3D_CLUSTER=atlas-p6 ATLAS_K3D_HTTP_PORT=8086 ATLAS_K3D_HTTPS_PORT=8446 make k3d-smoke`.

The smoke test, also run in CI, installs only the API and the website (`values-ci.yaml`, no operators) and checks
`/v1/health` and the home page through the ingress, which also proves the NetworkPolicies let traffic through:

```bash
make k3d-smoke
```

To try GitOps locally, `make k3d-argocd` installs Argo CD and the `dev` root application. The repository is
private, so first give Argo CD read access (a fine-grained, read-only token):
`argocd repo add https://github.com/Manuelma4/eu-disclosure-atlas.git --username Manuelma4 --password <token>`.

## Supply chain

`images.yml` runs on every pull request and every push to main:

1. A path filter decides, per image, whether its sources changed. Changed images are built (Buildx, GitHub
   Actions cache, SLSA provenance); unchanged ones are re-tagged from `:main`, keeping their digest and signature.
   Every commit therefore has a complete set of images tagged `sha-<7 hex>`, which previews and promotion rely on.
2. **Trivy** scans the pushed digest; the job fails on fixable critical vulnerabilities and reports high ones.
3. **Syft** writes an SPDX SBOM, kept as a workflow artifact and attached to the image as a cosign attestation.
4. **cosign** signs the digest keylessly with the workflow's GitHub OIDC identity (Sigstore Fulcio and Rekor):
   no keys to store or rotate.

The Go images are built the same way as soon as `services/<name>/Dockerfile` exists (workstream P3).

In the cluster, Kyverno's `atlas-signed-images` policy verifies that every `ghcr.io/manuelma4/atlas-*` image was
signed by `.github/workflows/images.yml` in this repository, and the other two policies refuse `latest` or
untagged images and containers without requests and a memory limit. Check by hand with:

```bash
cosign verify ghcr.io/manuelma4/atlas-python:sha-1a2b3c4 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/Manuelma4/eu-disclosure-atlas/'
```

## Deploy flow

Argo CD deploys what main says; nobody runs `helm` or `kubectl apply` against production.

1. A pull request merges into main; `images.yml` builds and signs `sha-<commit>`.
2. `deploy.yml` starts when the images workflow succeeds on main. It verifies the cosign signature of every
   image of that tag (identity: the images workflow on `refs/heads/main`) and opens a pull request that sets
   `global.image.tag` in `deploy/helm/atlas/values-prod.yaml`.
3. Merging that pull request is the deploy. Argo CD's `prod-atlas` application syncs it: rolling updates
   respect the PodDisruptionBudgets and readiness probes, Kyverno admits only signed images.
4. Sync order inside the root application: operators (wave -2), policies and cluster manifests (wave -1), then
   the Atlas release (wave 0).

Repository settings this needs: "Allow GitHub Actions to create and approve pull requests", and a `prod`
environment (optionally with required reviewers). Pull requests opened with `GITHUB_TOKEN` do not trigger other
workflows; the promotion only changes a values file, which `platform.yml` would validate on the next push.

## Rollbacks

- **Revert the promotion pull request** (or merge a new one): Argo CD syncs the previous tag. This is the normal
  path, because git stays the truth.
- **Promote an earlier tag** with `deploy.yml` → Run workflow → `tag: sha-<earlier commit>`; the signature check
  still applies.
- **Emergency:** Argo CD's History and Rollback (UI or `argocd app rollback prod-atlas <id>`) re-applies an
  earlier sync immediately. Automated sync would undo it on the next poll, so disable self-heal for
  `prod-atlas` first and follow up with a revert in git.
- **Data:** the umbrella chart keeps the lake volume on uninstall (`helm.sh/resource-policy: keep`), Strimzi
  claims are kept, and CloudNativePG restores from its object-storage backups (point in time) into a new cluster.

## Preview environments

Label a pull request `preview`. The `atlas-previews` ApplicationSet on the preview cluster (pull-request
generator, polled every two minutes) creates the Application `atlas-pr-<number>`:

- namespace `atlas-pr-<number>`, labelled for the restricted profile and the Kyverno policies;
- the chart from the pull request's head commit with `values-preview.yaml` and the images `sha-<head sha>`
  (built and signed for the pull request by `images.yml`);
- its own small Kafka and PostgreSQL, no live crawling (`ingestion.enabled=false`);
- `https://pr-<number>.preview.<domain>`, with a Let's Encrypt certificate (wildcard DNS from OpenTofu).

New commits update the preview; closing the pull request or removing the label deletes the Application and
everything in the namespace. The generator needs a read-only GitHub token in the Secret `github-previews`
(key `token`) in the `argocd` namespace. Known limit: the website's browser-side API URL is baked into the
image at build time (`NEXT_PUBLIC_ATLAS_API_URL`), so a preview's live tape calls the production API; its
server-rendered pages use the preview's own API.

## Secrets

Runtime secrets live in Scaleway Secret Manager and reach pods through External Secrets (`ExternalSecret`
objects in `values-prod.yaml`, the `scaleway` ClusterSecretStore in `deploy/platform/manifests/`). The single
bootstrap secret, the Secret Manager API key, is committed SOPS-encrypted with age
(`deploy/secrets/.sops.yaml`) and applied once per cluster:

```bash
sops --decrypt deploy/secrets/scaleway-secret-manager.enc.yaml | kubectl apply -f -
```

No plaintext secret is ever committed; `.env` and keys are git-ignored.

## Infrastructure (OpenTofu)

`infra/tofu/environments/{prod,preview}` compose three modules:

- `kapsule`: private network, Kapsule cluster with Cilium, an autoscaled and autohealed node pool, patch
  upgrades in a Sunday 03:00 window, and a reserved IP for the ingress load balancer;
- `object-storage`: private `bronze` (versioned, cold storage after 90 days, never published), `lake` and
  `backups` (expiring) buckets, plus the public-read `open-data` bucket with CORS in prod;
- `dns`: A records for the site, `api` and `stream` (prod) or `*.preview` (preview), and a CAA record.

Everything is in the EU (`fr-par`). State lives in a Scaleway bucket and is encrypted client-side (it contains
the admin kubeconfig). Nothing has been applied yet: CI only runs `tofu fmt -check` and `tofu validate`
(`make tofu-check`). First apply, once credentials exist:

```bash
cd infra/tofu/environments/prod
export SCW_ACCESS_KEY=... SCW_SECRET_KEY=... SCW_DEFAULT_PROJECT_ID=...
export AWS_ACCESS_KEY_ID="$SCW_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$SCW_SECRET_KEY" TF_VAR_state_passphrase=...
cp backend.hcl.example backend.hcl && tofu init -backend-config=backend.hcl
tofu plan -out prod.tfplan && tofu apply prod.tfplan
tofu output -raw kubeconfig > ~/.kube/atlas-prod && export KUBECONFIG=~/.kube/atlas-prod
helm install argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.10.5 -n argocd \
  --create-namespace -f deploy/argocd/install/values.yaml
kubectl apply -n argocd -f deploy/argocd/projects/atlas.yaml -f deploy/argocd/environments/prod.yaml
```

Before that, replace the placeholders: the domain `atlas.example.org` (values files, ApplicationSet, tfvars),
the Scaleway project id in the ClusterSecretStore, and the age recipient in `.sops.yaml`.

## Validation

| Check | Command | CI |
|---|---|---|
| Chart lint and schemas | `make deploy-validate` (helm lint `--strict`, kubeconform strict on Kubernetes 1.36 with operator CRD schemas) | `platform.yml` |
| Generated Argo CD apps | `python3 deploy/scripts/argocd_platform.py --check` | `platform.yml` |
| OpenTofu | `make tofu-check` | `platform.yml` |
| End to end on k3d | `make k3d-smoke` | `platform.yml` |
| Image scan, SBOM, signature | — | `images.yml` |
