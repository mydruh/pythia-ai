const BASE = import.meta.env.VITE_API_URL ?? '/api'

/** Заголовок аутентификации Telegram Mini App (в браузере без Telegram отсутствует). */
function authHeaders(): Record<string, string> {
  const initData = (window as any).Telegram?.WebApp?.initData
  return initData ? { 'X-Telegram-Init-Data': initData } : {}
}

async function req<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`${res.status} ${path}`)
  return res.json() as Promise<T>
}

async function reqWithTotal<T>(path: string): Promise<{ data: T; total: number }> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`${res.status} ${path}`)
  const total = parseInt(res.headers.get('X-Total-Count') ?? '0', 10)
  return { data: (await res.json()) as T, total }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

// ── types ─────────────────────────────────────────────────────────

export interface User {
  id: number
  telegram_id: number
  username: string | null
  first_name: string | null
  starting_balance: number
  virtual_balance: number
}

export interface WalletStats {
  telegram_id: number
  free_balance: number
  bots_balance: number
  equity: number
  total_deposited: number
  realized_pnl: number
  unrealized_pnl: number
  total_return_pct: number
  active_sessions: number
  open_positions: number
  closed_positions: number
}

export type BotStatus = 'active' | 'paused' | 'settling' | 'completed' | 'stopped'

export interface Bot {
  id: number
  name: string | null
  category: string | null        // null = все категории
  status: BotStatus
  starting_balance: number
  balance: number
  equity: number
  realized_pnl: number
  unrealized_pnl: number
  total_return_pct: number
  open_positions: number
  closed_positions: number
  sim_start: string | null
  sim_end: string | null
}

export interface Position {
  id: number
  session_id: number
  market_question: string
  market_url: string | null
  side: 'YES' | 'NO'
  token_outcome: string | null   // название команды/исхода если не Yes/No
  size_usdc: number
  entry_price: number
  current_price: number | null
  unrealized_pnl: number | null
  pnl: number | null
  status: 'open' | 'closed'
  reasoning: string | null
  opened_at: string
  closed_at: string | null
}

export interface Analysis {
  id: number
  session_id: number | null
  market_id: number
  model: string
  market_prob: number | null
  my_prob: number
  edge: number
  verdict: 'BUY_YES' | 'BUY_NO' | 'NEUTRAL'
  has_position: boolean
  block_reason: string | null    // почему BUY-сигнал не открыл позицию (null если вошёл/NEUTRAL)
  reasoning: string
  created_at: string
  market_question: string | null
  market_url: string | null
  token_outcome: string | null
}

export interface CycleInfo {
  next_cycle_at: string | null
  interval_minutes: number
}

export interface PnlPoint {
  closed_at: string
  pnl: number
  cumulative_pnl: number
}

export interface Market {
  id: number
  question: string
  category: string | null
  volume: number | null
  close_time: string | null
  url: string | null
}

export interface Category {
  category: string
  count: number
}

export interface CreateBotBody {
  name?: string | null
  category?: string | null
  budget: number
  days: number
}

// ── русские подписи категорий (UI-ярлыки, не перевод лотов) ────────

export const CATEGORY_LABELS: Record<string, string> = {
  Politics: 'Политика',
  Crypto: 'Крипта',
  Sports: 'Спорт',
  Economy: 'Экономика',
  Tech: 'Технологии',
  Culture: 'Поп-культура',
  Science: 'Наука',
  World: 'Мир',
  'Прочее': 'Прочее',
}

export function catLabel(cat: string | null): string {
  if (!cat) return 'Все категории'
  return CATEGORY_LABELS[cat] ?? cat
}

// ── api ───────────────────────────────────────────────────────────

export const api = {
  // wallet
  walletStart: (body: { telegram_id: number; deposit?: number }) => post<User>(`/users/start`, body),
  walletStats: (uid: number) => req<WalletStats>(`/users/${uid}/stats`),
  deposit: (uid: number, amount: number) => post<User>(`/users/${uid}/deposit`, { amount }),
  userPositions: (uid: number, status?: string) =>
    req<Position[]>(`/users/${uid}/positions${status ? `?status=${status}` : ''}`),
  userPnl: (uid: number) => req<PnlPoint[]>(`/users/${uid}/pnl_history`),

  // sessions (боты)
  listBots: (uid: number) => req<Bot[]>(`/users/${uid}/sessions`),
  createBot: (uid: number, body: CreateBotBody) => post<Bot>(`/users/${uid}/sessions`, body),
  botStats: (uid: number, sid: number) => req<Bot>(`/users/${uid}/sessions/${sid}`),
  botPositions: (uid: number, sid: number, status?: string) =>
    req<Position[]>(`/users/${uid}/sessions/${sid}/positions${status ? `?status=${status}` : ''}`),
  botAnalyses: (uid: number, sid: number, offset = 0, limit = 20) =>
    reqWithTotal<Analysis[]>(`/users/${uid}/sessions/${sid}/analyses?limit=${limit}&offset=${offset}`),
  cycleNext: () => req<CycleInfo>(`/cycle/next`),
  botPnl: (uid: number, sid: number) => req<PnlPoint[]>(`/users/${uid}/sessions/${sid}/pnl_history`),
  pauseBot: (uid: number, sid: number) => post<Bot>(`/users/${uid}/sessions/${sid}/pause`, {}),
  resumeBot: (uid: number, sid: number) => post<Bot>(`/users/${uid}/sessions/${sid}/resume`, {}),
  stopBot: (uid: number, sid: number) => post<Bot>(`/users/${uid}/sessions/${sid}/stop`, {}),

  // markets / categories
  markets: (params: { category?: string | null; sort?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams()
    if (params.category) q.set('category', params.category)
    if (params.sort) q.set('sort', params.sort)
    q.set('limit', String(params.limit ?? 20))
    q.set('offset', String(params.offset ?? 0))
    return reqWithTotal<Market[]>(`/markets?${q.toString()}`)
  },
  categories: () => req<Category[]>(`/categories`),
}
