{{- define "base-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "base-stack.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "base-stack.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "base-stack.labels" -}}
app.kubernetes.io/name: {{ include "base-stack.name" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "base-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "base-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "base-stack.deployableFullname" -}}
{{- $root := .root -}}
{{- $item := .item -}}
{{- printf "%s-%s" (include "base-stack.fullname" $root) $item.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
