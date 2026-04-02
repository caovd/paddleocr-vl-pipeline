{{/*
Expand the name of the chart.
*/}}
{{- define "paddleocr-vl-pipeline.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "paddleocr-vl-pipeline.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "paddleocr-vl-pipeline.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "paddleocr-vl-pipeline.labels" -}}
helm.sh/chart: {{ include "paddleocr-vl-pipeline.chart" . }}
{{ include "paddleocr-vl-pipeline.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — includes hpe-ezua labels (BYOA requirement)
*/}}
{{- define "paddleocr-vl-pipeline.selectorLabels" -}}
{{ include "hpe-ezua.labels" . }}
app.kubernetes.io/name: {{ include "paddleocr-vl-pipeline.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "paddleocr-vl-pipeline.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "paddleocr-vl-pipeline.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* ========================================================================
     Component-specific name helpers
     ======================================================================== */}}

{{/*
Gotenberg fully qualified name
*/}}
{{- define "paddleocr-vl-pipeline.gotenberg.fullname" -}}
{{- printf "%s-gotenberg" (include "paddleocr-vl-pipeline.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Gotenberg selector labels
*/}}
{{- define "paddleocr-vl-pipeline.gotenberg.selectorLabels" -}}
{{ include "hpe-ezua.labels" . }}
app.kubernetes.io/name: {{ include "paddleocr-vl-pipeline.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: gotenberg
{{- end }}

{{/*
PaddleOCR VL API fully qualified name
*/}}
{{- define "paddleocr-vl-pipeline.vlapi.fullname" -}}
{{- printf "%s-vl-api" (include "paddleocr-vl-pipeline.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
PaddleOCR VL API selector labels
*/}}
{{- define "paddleocr-vl-pipeline.vlapi.selectorLabels" -}}
{{ include "hpe-ezua.labels" . }}
app.kubernetes.io/name: {{ include "paddleocr-vl-pipeline.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: vl-api
{{- end }}

{{/*
Gotenberg internal URL (for use by VL API or orchestration layer)
*/}}
{{- define "paddleocr-vl-pipeline.gotenberg.url" -}}
http://{{ include "paddleocr-vl-pipeline.gotenberg.fullname" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.gotenberg.service.port }}
{{- end }}
