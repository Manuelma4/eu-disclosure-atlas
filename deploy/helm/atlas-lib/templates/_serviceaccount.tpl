{{- define "atlas-lib.serviceaccount" -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
automountServiceAccountToken: false
---
{{- end -}}
