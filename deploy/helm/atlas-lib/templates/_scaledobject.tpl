{{/* KEDA ScaledObject on Kafka consumer lag: scales the consumer group with its backlog, to zero when idle. */}}
{{- define "atlas-lib.scaledobject" -}}
{{- if .Values.keda.enabled }}
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    name: {{ include "atlas-lib.name" . }}
  minReplicaCount: {{ .Values.keda.minReplicas }}
  maxReplicaCount: {{ .Values.keda.maxReplicas }}
  pollingInterval: {{ .Values.keda.pollingInterval | default 15 }}
  cooldownPeriod: {{ .Values.keda.cooldownPeriod | default 300 }}
  triggers:
    {{- range .Values.keda.topics }}
    - type: kafka
      metadata:
        bootstrapServers: {{ include "atlas-lib.kafkaBootstrap" $ }}
        consumerGroup: {{ $.Values.keda.consumerGroup | default (include "atlas-lib.name" $) }}
        topic: {{ . }}
        lagThreshold: {{ $.Values.keda.lagThreshold | default 50 | quote }}
        offsetResetPolicy: earliest
    {{- end }}
---
{{- end }}
{{- end -}}
