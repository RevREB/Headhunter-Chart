{{- define "headhunter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "headhunter.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "headhunter.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "headhunter.core.fullname" -}}
{{- printf "%s-core" (include "headhunter.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "headhunter.webmcp.fullname" -}}
{{- printf "%s-webmcp" (include "headhunter.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "headhunter.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "headhunter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "headhunter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "headhunter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "headhunter.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "headhunter.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
