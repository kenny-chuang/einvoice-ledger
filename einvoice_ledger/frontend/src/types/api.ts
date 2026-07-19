export interface SyncEvent {
  stage: string
  status: string
  attempt: number
  error_code: string | null
  metadata: Record<string, unknown>
  started_at: string
  finished_at: string | null
}

export interface SyncRunDetail {
  id: number
  months: string[]
  status: string
  current_stage: string
  attempt_count: number
  message: string
  started_at: string
  finished_at: string | null
  stats: Record<string, unknown>
  events: SyncEvent[]
}

export interface BudgetSummaryItem {
  category: string
  limit: number
  spent: number
  remaining: number
  usage_percent: number
  forecast: number
}

export interface BudgetSummary {
  month: string
  items: BudgetSummaryItem[]
  total_limit: number
  total_spent: number
  total_remaining: number
  unallocated_discount_total: number
}

export interface PriceAlert {
  product_id: number
  target_price: number | null
  notify_new_low: boolean
  enabled: boolean
  updated_at: string
}

export interface DataQualityIssue {
  id: number
  issue_type: string
  severity: string
  confidence: string
  repair_rule: string | null
  message: string
  status: string
  invoice_line_id: number | null
  invoice_date: string | null
  invoice_number: string | null
  store: string
  created_at: string
  resolved_at: string | null
}
