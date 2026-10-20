{{/*
Deployment with the `restricted` Pod Security profile: non-root, no privilege escalation, every capability
dropped, RuntimeDefault seccomp and a read-only root filesystem (writable paths are explicit emptyDirs).
*/}}
{{- define "atlas-lib.workload" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "atlas-lib.name" . }}
  labels:
    {{- include "atlas-lib.labels" . | nindent 4 }}
spec:
  {{- if not (or .Values.autoscaling.enabled .Values.keda.enabled) }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  revisionHistoryLimit: 5
  {{- with .Values.strategy }}
  strategy:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "atlas-lib.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "atlas-lib.labels" . | nindent 8 }}
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      serviceAccountName: {{ include "atlas-lib.name" . }}
      automountServiceAccountToken: false
      enableServiceLinks: false
      securityContext:
        runAsNonRoot: true
        runAsUser: {{ .Values.securityContext.runAsUser }}
        runAsGroup: {{ .Values.securityContext.runAsUser }}
        fsGroup: {{ .Values.securityContext.runAsUser }}
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      {{- with .Values.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- tpl (toYaml .) $ | nindent 8 }}
      {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds | default 30 }}
      containers:
        - name: {{ include "atlas-lib.name" . }}
          image: {{ include "atlas-lib.image" . | quote }}
          imagePullPolicy: {{ include "atlas-lib.pullPolicy" . }}
          {{- with .Values.command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.args }}
          args:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- if .Values.containerPort }}
          ports:
            - name: http
              containerPort: {{ .Values.containerPort }}
              protocol: TCP
          {{- end }}
          {{- if or .Values.env .Values.envFromSecret }}
          env:
            {{- range $name, $value := .Values.env }}
            - name: {{ $name }}
              value: {{ tpl (toString $value) $ | quote }}
            {{- end }}
          {{- end }}
          {{- if .Values.envFromSecret }}
          envFrom:
            - secretRef:
                name: {{ .Values.envFromSecret }}
                optional: true
          {{- end }}
          {{- with .Values.probes.startup }}
          startupProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.probes.liveness }}
          livenessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.probes.readiness }}
          readinessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          resources:
            {{- required (printf "%s: resources are required" (include "atlas-lib.name" .)) .Values.resources | toYaml | nindent 12 }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop: ["ALL"]
            seccompProfile:
              type: RuntimeDefault
          volumeMounts:
            {{- range $i, $path := .Values.writablePaths }}
            - name: scratch-{{ $i }}
              mountPath: {{ $path }}
            {{- end }}
            {{- if .Values.lake.enabled }}
            - name: lake
              mountPath: /data/lake
              readOnly: {{ .Values.lake.readOnly }}
            {{- end }}
      volumes:
        {{- range $i, $path := .Values.writablePaths }}
        - name: scratch-{{ $i }}
          emptyDir:
            sizeLimit: {{ $.Values.scratchSizeLimit | default "256Mi" }}
        {{- end }}
        {{- if .Values.lake.enabled }}
        - name: lake
          persistentVolumeClaim:
            claimName: {{ .Values.lake.claimName }}
            readOnly: {{ .Values.lake.readOnly }}
        {{- end }}
---
{{- end -}}
