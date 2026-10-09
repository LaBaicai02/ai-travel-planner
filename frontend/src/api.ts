import type { Activity, ApiFailure, Currency, Health, Plan } from './types'

export class ApiError extends Error {
  readonly details: ApiFailure | undefined
  constructor(message: string, details?: ApiFailure) {
    super(message)
    this.name = 'ApiError'
    this.details = details
  }
}

const currencies = ['CNY', 'USD', 'EUR', 'JPY']
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string')
const amount = (v: unknown) => typeof v === 'string' && /^\d+\.\d{2}$/.test(v) && Number.isFinite(Number(v))
const nullableAmount = (v: unknown) => v === null || amount(v)
export function safeSourceUrl(value: string): boolean {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password } catch { return false }
}
function activity(v: unknown): v is Activity {
  if (!record(v)) return false
  return ['time', 'location', 'description', 'cost_basis'].every(k => typeof v[k] === 'string')
    && nullableAmount(v.estimated_cost) && ['reference', 'estimated', 'unknown', 'demo'].includes(String(v.cost_status))
    && (v.estimated_cost === null) === (v.cost_status === 'unknown')
    && ['reported', 'unknown'].includes(String(v.opening_hours_status))
    && (v.opening_hours === null || typeof v.opening_hours === 'string')
    && (v.opening_hours === null) === (v.opening_hours_status === 'unknown')
    && Array.isArray(v.sources) && v.sources.every(s => record(s) && typeof s.id === 'string' && typeof s.title === 'string' && typeof s.url === 'string' && safeSourceUrl(s.url) && typeof s.retrieved_at === 'string')
}
function plan(v: unknown): v is Plan {
  if (!record(v) || !['demo', 'live'].includes(String(v.mode))) return false
  return v.demo === (v.mode === 'demo') && typeof v.destination === 'string'
    && Number.isInteger(v.travelers) && currencies.includes(String(v.currency))
    && amount(v.budget) && nullableAmount(v.total_cost) && amount(v.known_cost_subtotal)
    && typeof v.notice === 'string' && typeof v.request_id === 'string' && strings(v.warnings)
    && (v.mode !== 'live' || v.currency === 'CNY')
    && Array.isArray(v.days) && v.days.length > 0 && v.days.length <= 14 && v.days.every(d => record(d)
      && Number.isInteger(d.day) && typeof d.date === 'string' && typeof d.title === 'string'
      && nullableAmount(d.estimated_cost) && amount(d.known_cost_subtotal)
      && Array.isArray(d.activities) && d.activities.length > 0 && d.activities.length <= 12 && d.activities.every(a => activity(a)
        && (v.mode !== 'live' || (a.sources.length > 0 && a.cost_status !== 'demo'))))
}
async function request(path: string, init: RequestInit, timeoutMs: number): Promise<unknown> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, { ...init, signal: controller.signal })
    const data: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      const error = record(data) && record(data.error) ? data.error : null
      const message = error && typeof error.message === 'string' ? error.message : response.status === 422 ? '输入信息未通过校验，请检查日期、预算及其他字段。' : '生成失败，请稍后重试。'
      const details = error && typeof error.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(error.code)
        && typeof error.retryable === 'boolean' && typeof error.request_id === 'string'
        && /^[a-zA-Z0-9_-]{1,64}$/.test(error.request_id) ? { code: error.code, message, retryable: error.retryable, request_id: error.request_id } : undefined
      throw new ApiError(message, details)
    }
    return data
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw new ApiError('请求超时，请检查后端服务后重试。')
    if (err instanceof TypeError) throw new ApiError('暂时无法连接服务，请确认后端已启动后重试。')
    throw err
  } finally { window.clearTimeout(timeout) }
}
export async function readHealth(): Promise<Health> {
  const v = await request('/api/health', {}, 5000)
  if (!record(v) || !['demo', 'live'].includes(String(v.mode)) || v.demo !== (v.mode === 'demo')
    || !['ok', 'unavailable'].includes(String(v.status)) || typeof v.configured !== 'boolean'
    || typeof v.ready !== 'boolean' || typeof v.live_integration_available !== 'boolean'
    || !strings(v.supported_currencies) || !v.supported_currencies.length
    || !v.supported_currencies.every(c => currencies.includes(c))
    || !strings(v.configuration_issues) || typeof v.message !== 'string') throw new Error('服务状态格式错误，请检查后端。')
  return v as Health
}
export async function generatePlan(body: unknown, mode: 'demo' | 'live'): Promise<Plan> {
  const v = await request('/api/itineraries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, mode === 'demo' ? 15000 : 130000)
  if (!plan(v) || v.mode !== mode) throw new Error('行程数据格式或服务模式不一致，请重试。')
  return v
}
export const currencyLabel = (value: Currency) => ({ CNY: 'CNY 人民币', USD: 'USD 美元', EUR: 'EUR 欧元', JPY: 'JPY 日元' }[value])
