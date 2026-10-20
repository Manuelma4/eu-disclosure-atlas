{{- define "atlas-lib.service" -}}
{{- if .Values.containerPort }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "atlas-lib.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: {{ .Values.service.port | default .Values.containerPort }}
      targetPort: http
      protocol: TCP
---
{{- end }}
{{- end -}}
