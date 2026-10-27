variable "project" {
  type    = string
  default = "ags"
}

variable "env" {
  type = string
}

variable "region" {
  description = "Fed from AWS_REGION: CI passes -var region=..., local shells export TF_VAR_region=$AWS_REGION."
  type        = string
  default     = "eu-west-1"
}

variable "image_uri" {
  description = "ECR image by digest; CI passes it."
  type        = string
}

variable "schedules_enabled" {
  description = "Master switch for every schedule, the cycle included."
  type        = bool
  default     = false
}

variable "limits" {
  description = "Guardrail, book and cycle-operation parameters (ARCHITECTURE.md). Tuned here, never in code."
  type        = map(string)
  default = {
    RETRY_WINDOW_HOURS    = "3" # a held cycle may be retried up to this long after its slot (max 6)
    MIN_CYCLES_OF_CREDIT  = "4" # alert when DeepSeek / Tavily credit covers fewer cycles than this
    PER_COMMODITY_CAP     = "1.0"
    GRAIN_BUCKET_CAP      = "1.5"
    GROSS_LEVERAGE_CAP    = "3.0"
    MARGIN_BUFFER         = "0.40"
    MIN_RESIZE_FRACTION   = "0.20"
    DRAWDOWN_BREAKER      = "0.60"
    STARTING_EQUITY_USD   = "10000"
    SHADOW_RISK_BUDGET    = "0.01"
    STOP_OUT_MARGIN_LEVEL = "0.50"
  }
}
