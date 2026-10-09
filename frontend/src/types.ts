export type Mode = 'demo' | 'live'
export type Currency = 'CNY' | 'USD' | 'EUR' | 'JPY'
export type Form = { destination: string; departure_date: string; days: string; travelers: string; budget: string; currency: Currency; interests: string[] }
export type Source = { id: string; title: string; url: string; retrieved_at: string }
export type Activity = {
  time: string; location: string; description: string; estimated_cost: string | null
  cost_status: 'reference' | 'estimated' | 'unknown' | 'demo'; cost_basis: string
  sources: Source[]; opening_hours: string | null; opening_hours_status: 'reported' | 'unknown'
}
export type DayPlan = { day: number; date: string; title: string; estimated_cost: string | null; known_cost_subtotal: string; activities: Activity[] }
export type Plan = {
  demo: boolean; mode: Mode; destination: string; travelers: number; currency: Currency; budget: string
  total_cost: string | null; known_cost_subtotal: string; notice: string; warnings: string[]; request_id: string; days: DayPlan[]
}
export type Health = {
  status: 'ok' | 'unavailable'; mode: Mode; demo: boolean; configured: boolean; ready: boolean
  live_integration_available: boolean; supported_currencies: Currency[]; configuration_issues: string[]; message: string
}
