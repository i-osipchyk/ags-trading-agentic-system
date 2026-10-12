variable "image_tag" {
  description = "Commit SHA of the image pushed by the deploy workflow."
  type        = string
}

variable "limits" {
  description = "Guardrail and book parameters (ARCHITECTURE.md). Tuned here, never in code."
  type        = map(string)
  default = {
    PER_COMMODITY_CAP       = "1.0"
    GRAIN_BUCKET_CAP        = "1.5"
    GROSS_LEVERAGE_CAP      = "3.0"
    MARGIN_BUFFER           = "0.40"
    MIN_RESIZE_FRACTION     = "0.20"
    DRAWDOWN_BREAKER        = "0.80"
    STARTING_EQUITY_USD     = "10000"
    SHADOW_RISK_BUDGET      = "0.01"
    STOP_OUT_MARGIN_LEVEL   = "0.50"
  }
}

variable "llm_prices_per_mtok" {
  description = "LLM $ per million tokens for the digest: cache_hit, cache_miss, output. No default on purpose."
  type        = object({ cache_hit = number, cache_miss = number, output = number })
}
