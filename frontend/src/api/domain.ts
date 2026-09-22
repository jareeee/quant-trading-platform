export interface Page<T> {
  items: T[]
  limit: number
  offset: number
  total: number
}

export interface StatusResponse {
  status: string
  healthy: boolean
  checked_at: string
  heartbeat: {
    service_name: string
    state: string | null
    last_seen_at: string | null
    age_seconds: number | null
    reason: string | null
  }
  queue: { pending: number; processing: number; failed: number }
}

export interface Asset {
  id: number
  exchange_config_id: number
  symbol: string
  base_asset: string
  quote_asset: string
  enabled: boolean
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface Position {
  id: number
  exchange_config_id: number
  asset_config_id: number
  quantity: string
  average_entry_price: string
  stored_realized_pnl: string
  created_at: string
  updated_at: string
}

export interface StrategyRun {
  id: number
  asset_config_id: number
  strategy_name: string
  status: string
  idempotency_key: string
  parameters: Record<string, unknown>
  scheduled_boundary: string
  started_at: string
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface Order {
  id: number
  exchange_config_id: number
  strategy_run_id: number | null
  signal_id: number | null
  client_order_id: string
  exchange_order_id: string | null
  symbol: string
  side: string
  order_type: string
  status: string
  quantity: string
  price: string | null
  filled_quantity: string
  submitted_at: string | null
  created_at: string
  updated_at: string
}

export interface Fill {
  id: number
  order_id: number
  exchange_fill_id: string
  quantity: string
  price: string
  fee_amount: string
  fee_currency: string | null
  executed_at: string
  created_at: string
  updated_at: string
}
