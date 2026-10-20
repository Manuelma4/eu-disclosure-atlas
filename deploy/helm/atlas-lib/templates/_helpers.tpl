{{/*
Names. Every service keeps its chart name as its Kubernetes name, so peers can address it by a stable DNS name
(query-api, web, ...) in any namespace, including pull-request preview namespaces.
*/}}
{{- define "atlas-lib.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "atlas-lib.selectorLabels" -}}
app.kubernetes.io/name: {{ include "atlas-lib.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "atlas-lib.labels" -}}
{{ include "atlas-lib.selectorLabels" . }}
app.kubernetes.io/version: {{ include "atlas-lib.imageTag" . | quote }}
app.kubernetes.io/component: {{ .Values.component | default "service" }}
app.kubernetes.io/part-of: atlas
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Image tag: the service's own tag, else global.image.tag (set per environment), else the chart appVersion. */}}
{{- define "atlas-lib.imageTag" -}}
{{- $global := (.Values.global | default dict).image | default dict -}}
{{- .Values.image.tag | default $global.tag | default .Chart.AppVersion -}}
{{- end -}}

{{/* Image reference. A digest, when given, pins the image (the form Kyverno verifies); `latest` is refused. */}}
{{- define "atlas-lib.image" -}}
{{- $global := (.Values.global | default dict).image | default dict -}}
{{- $registry := .Values.image.registry | default $global.registry | default "ghcr.io/manuelma4" -}}
{{- $tag := include "atlas-lib.imageTag" . -}}
{{- if eq $tag "latest" -}}
{{- fail (printf "%s: the image tag `latest` is not allowed; pin a version or a sha tag" (include "atlas-lib.name" .)) -}}
{{- end -}}
{{- if .Values.image.digest -}}
{{- printf "%s/%s:%s@%s" $registry .Values.image.repository $tag .Values.image.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" $registry .Values.image.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "atlas-lib.pullPolicy" -}}
{{- $global := (.Values.global | default dict).image | default dict -}}
{{- .Values.image.pullPolicy | default $global.pullPolicy | default "IfNotPresent" -}}
{{- end -}}

{{/*
Kafka bootstrap servers: the service's own value, else the platform-wide one, else the Strimzi cluster `atlas`
in the release namespace. Fully qualified, because KEDA resolves it from its own namespace.
*/}}
{{- define "atlas-lib.kafkaBootstrap" -}}
{{- $global := (.Values.global | default dict).kafka | default dict -}}
{{- .Values.kafka.bootstrapServers | default $global.bootstrapServers | default (printf "atlas-kafka-bootstrap.%s.svc:9092" .Release.Namespace) -}}
{{- end -}}

{{/* Renders every resource of a service chart. Each service chart's templates/all.yaml is just this include. */}}
{{- define "atlas-lib.all" -}}
{{- if .Values.enabled }}
{{ include "atlas-lib.serviceaccount" . }}
{{ include "atlas-lib.workload" . }}
{{ include "atlas-lib.service" . }}
{{ include "atlas-lib.pdb" . }}
{{ include "atlas-lib.networkpolicy" . }}
{{ include "atlas-lib.hpa" . }}
{{ include "atlas-lib.scaledobject" . }}
{{ include "atlas-lib.ingress" . }}
{{- end }}
{{- end -}}
