# 0012: Kubernetes, GitOps and infrastructure as code

Status: accepted (2026-10-20); supersedes the VM-based delivery in the first architecture

## Context
Eight services, Kafka, PostgreSQL and object storage need repeatable environments, safe rollouts, autoscaling
and preview environments for pull requests, operated by one person.

## Decision
- **Kubernetes** everywhere: k3d on laptops and in CI, a managed cluster in production.
- **Production on a European cloud:** Scaleway Kapsule (managed Kubernetes, free control plane) and Scaleway
  Object Storage (S3 API), described with **OpenTofu** modules. Keeping the data in the EU fits a project about
  EU regulation.
- **Packaging:** one Helm chart per service plus an umbrella chart. Every pod gets probes, resource
  requests and limits, a PodDisruptionBudget, `restricted` Pod Security and NetworkPolicies (default deny).
- **GitOps with Argo CD:** an app-of-apps for environments, and an ApplicationSet pull-request generator that
  creates an ephemeral namespace per pull request.
- **Operators:** Strimzi (Kafka), CloudNativePG (PostgreSQL with backups to object storage), cert-manager (TLS),
  External Secrets with SOPS-encrypted values, KEDA (autoscaling on Kafka lag), Kyverno (policies).
- **Supply chain:** images built in GitHub Actions, scanned (Trivy), with an SBOM (Syft), signed (cosign) and
  pushed to GHCR. Kyverno only admits signed images.

## Consequences
- Every environment is declared in git and reproducible from scratch.
- More platform components to keep current, managed by Renovate and Argo CD.
