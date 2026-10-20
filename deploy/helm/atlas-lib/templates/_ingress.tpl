{{- define "atlas-lib.ingress" -}}
{{- if and .Values.ingress.enabled .Values.containerPort }}
{{- $global := .Values.global | default dict }}
{{- $host := tpl .Values.ingress.host . }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
  {{- if or .Values.ingress.annotations ($global.ingress | default dict).clusterIssuer }}
  annotations:
    {{- with ($global.ingress | default dict).clusterIssuer }}
    cert-manager.io/cluster-issuer: {{ . }}
    {{- end }}
    {{- with .Values.ingress.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  {{- end }}
spec:
  {{- with ($global.ingress | default dict).className }}
  ingressClassName: {{ . }}
  {{- end }}
  {{- if ($global.ingress | default dict).tls }}
  tls:
    - hosts: [{{ $host | quote }}]
      secretName: {{ include "atlas-lib.name" . }}-tls
  {{- end }}
  rules:
    - host: {{ $host | quote }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ include "atlas-lib.name" . }}
                port:
                  name: http
---
{{- end }}
{{- end -}}
