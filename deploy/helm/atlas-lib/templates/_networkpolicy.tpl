{{/*
Allow-list NetworkPolicy for one service. The namespace itself is default-deny (umbrella chart), so a pod only
receives traffic from the peers listed in networkPolicy.ingressFrom and only reaches DNS plus the egress
targets it declares (peer services, Kafka, PostgreSQL, the public internet).
*/}}
{{- define "atlas-lib.networkpolicy" -}}
{{- if .Values.networkPolicy.enabled }}
{{- $np := .Values.networkPolicy }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "atlas-lib.selectorLabels" . | nindent 6 }}
  policyTypes: ["Ingress", "Egress"]
  {{- if and .Values.containerPort (or $np.ingressFrom $np.allowIngressController $np.allowMonitoring) }}
  ingress:
    - ports:
        - port: http
          protocol: TCP
      from:
        {{- range $np.ingressFrom }}
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: {{ . }}
        {{- end }}
        {{- if $np.allowIngressController }}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $np.ingressControllerNamespace }}
        {{- end }}
        {{- if $np.allowMonitoring }}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $np.monitoringNamespace }}
        {{- end }}
  {{- else }}
  ingress: []
  {{- end }}
  egress:
    - ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
      to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
    {{- range $np.egressTo }}
    - ports:
        - port: {{ .port }}
          protocol: TCP
      to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: {{ .name }}
    {{- end }}
    {{- if $np.egressKafka }}
    - ports:
        - port: 9092
          protocol: TCP
      to:
        - podSelector:
            matchLabels:
              strimzi.io/kind: Kafka
    {{- end }}
    {{- if $np.egressPostgres }}
    - ports:
        - port: 5432
          protocol: TCP
      to:
        - podSelector:
            matchLabels:
              cnpg.io/podRole: instance
    {{- end }}
    {{- if $np.egressInternet }}
    - ports:
        - port: 443
          protocol: TCP
        - port: 80
          protocol: TCP
      to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"]
    {{- end }}
---
{{- end }}
{{- end -}}
