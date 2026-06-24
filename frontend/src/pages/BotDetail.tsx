import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, catLabel, type Analysis } from '../api/client'
import { getTelegramId } from '../hooks/useUser'
import { Loader, Empty, Button, card, Badge, STATUS_META } from '../components/ui'
import PositionCard from '../components/PositionCard'
import PnlChart from '../components/PnlChart'

type Tab = 'considering' | 'open' | 'closed'
const TABS: { key: Tab; label: string }[] = [
  { key: 'considering', label: 'Сигналы AI' },
  { key: 'open', label: 'Участвует' },
  { key: 'closed', label: 'Участвовала' },
]

export default function BotDetail({ sid, onBack }: { sid: number; onBack: () => void }) {
  const uid = getTelegramId()!
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('open')

  const { data: bot, isLoading } = useQuery({ queryKey: ['bot', uid, sid], queryFn: () => api.botStats(uid, sid), refetchInterval: 15_000 })
  const { data: pnl = [] } = useQuery({ queryKey: ['botPnl', uid, sid], queryFn: () => api.botPnl(uid, sid) })
  const { data: analyses = [] } = useQuery({ queryKey: ['botAnalyses', uid, sid], queryFn: () => api.botAnalyses(uid, sid), enabled: tab === 'considering' })
  const { data: openPos = [] } = useQuery({ queryKey: ['botPos', uid, sid, 'open'], queryFn: () => api.botPositions(uid, sid, 'open'), enabled: tab === 'open' })
  const { data: closedPos = [] } = useQuery({ queryKey: ['botPos', uid, sid, 'closed'], queryFn: () => api.botPositions(uid, sid, 'closed'), enabled: tab === 'closed' })

  const refresh = () => qc.invalidateQueries()
  const pauseMut = useMutation({ mutationFn: () => api.pauseBot(uid, sid), onSuccess: refresh })
  const resumeMut = useMutation({ mutationFn: () => api.resumeBot(uid, sid), onSuccess: refresh })
  const stopMut = useMutation({ mutationFn: () => api.stopBot(uid, sid), onSuccess: refresh })

  if (isLoading || !bot) return <div style={{ paddingBottom: 88 }}><BackBar onBack={onBack} /><Loader /></div>

  const [statusLabel, statusColor] = STATUS_META[bot.status] ?? [bot.status, '#94a3b8']
  const retColor = bot.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)'
  const terminal = bot.status === 'completed' || bot.status === 'stopped'

  return (
    <div style={{ paddingBottom: 88 }}>
      <BackBar onBack={onBack} />

      <div style={{ padding: '0 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <div style={{ fontSize: 20, fontWeight: 800 }}>{bot.name || catLabel(bot.category)}</div>
          <Badge color={statusColor}>{statusLabel}</Badge>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>
          {catLabel(bot.category)}{bot.sim_end ? ` · до ${new Date(bot.sim_end).toLocaleDateString('ru')}` : ''}
        </div>
      </div>

      {/* Метрики бюджета */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '14px 16px' }}>
        <Tile label="Капитал" value={`$${bot.equity.toFixed(2)}`} sub={`бюджет $${bot.starting_balance.toFixed(0)}`} />
        <Tile label="Доходность" value={`${bot.total_return_pct >= 0 ? '+' : ''}${bot.total_return_pct.toFixed(2)}%`} color={retColor} />
        <Tile label="Заработано/потеряно" value={fmtSigned(bot.equity - bot.starting_balance)} color={pnlColor(bot.equity - bot.starting_balance)} sub="итого с открытыми" />
        <Tile label="Реализовано" value={fmtSigned(bot.realized_pnl)} color={pnlColor(bot.realized_pnl)} sub="закрытые ставки" />
      </div>

      {/* Управление */}
      {!terminal && (
        <div style={{ display: 'flex', gap: 10, padding: '0 16px 8px' }}>
          {bot.status === 'active' &&
            <Button variant="ghost" onClick={() => pauseMut.mutate()} disabled={pauseMut.isPending}>⏸ Пауза</Button>}
          {bot.status === 'paused' &&
            <Button variant="ghost" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>▶ Возобновить</Button>}
          <Button variant="danger" onClick={() => stopMut.mutate()} disabled={stopMut.isPending}>⏹ Остановить</Button>
        </div>
      )}

      {pnl.length > 0 && (
        <div style={{ padding: '6px 16px 12px' }}>
          <PnlChart data={pnl} />
        </div>
      )}

      {/* Вкладки */}
      <div style={{ display: 'flex', gap: 8, padding: '4px 16px 12px' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            flex: 1, padding: '9px 0', borderRadius: 10, cursor: 'pointer', fontSize: 12.5, fontWeight: 700,
            border: `1px solid ${tab === t.key ? 'transparent' : 'var(--border)'}`,
            background: tab === t.key ? 'linear-gradient(135deg, var(--accent), #818cf8)' : 'transparent',
            color: tab === t.key ? '#fff' : 'var(--muted)',
          }}>{t.label}</button>
        ))}
      </div>

      <div style={{ padding: '0 16px' }}>
        {tab === 'considering' && (() => {
          const signals = analyses.filter(a => a.verdict !== 'NEUTRAL')
          return signals.length === 0
            ? <Empty>AI ещё не выдавал сигналов BUY.</Empty>
            : signals.map(a => <AnalysisRow key={a.id} a={a} />)
        })()}
        {tab === 'open' && (
          openPos.length === 0 ? <Empty>Нет открытых позиций.</Empty>
            : openPos.map(p => <PositionCard key={p.id} pos={p} />)
        )}
        {tab === 'closed' && (
          closedPos.length === 0 ? <Empty>Нет закрытых позиций.</Empty>
            : closedPos.map(p => <PositionCard key={p.id} pos={p} />)
        )}
      </div>
    </div>
  )
}

function BackBar({ onBack }: { onBack: () => void }) {
  return (
    <div style={{ padding: '16px 16px 8px' }}>
      <button onClick={onBack} style={{
        background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer',
        fontSize: 14, fontWeight: 600, padding: 0,
      }}>← Назад к ботам</button>
    </div>
  )
}

const VERDICT_COLOR: Record<Analysis['verdict'], string> = { BUY_YES: '#22c55e', BUY_NO: '#ef4444', NEUTRAL: '#94a3b8' }

function verdictLabel(a: Analysis): string {
  if (a.verdict === 'NEUTRAL') return 'пропущен'
  const side = a.verdict === 'BUY_YES' ? 'YES' : 'NO'
  return a.has_position ? `ВОШЁЛ · ${side}` : `заблокирован · ${side}`
}

function verdictColor(a: Analysis): string {
  if (a.verdict === 'NEUTRAL') return '#94a3b8'
  if (!a.has_position) return '#f59e0b'   // оранжевый — хотел войти, но риск-менеджер отказал
  return VERDICT_COLOR[a.verdict]
}

function AnalysisRow({ a }: { a: Analysis }) {
  const color = verdictColor(a)
  return (
    <div style={{ ...card, marginBottom: 10 }}>
      {/* Название лота */}
      {a.market_question && (
        <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.4, marginBottom: 8 }}>
          {a.market_url
            ? <a href={a.market_url} target="_blank" rel="noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>{a.market_question}</a>
            : a.market_question}
          {a.token_outcome && (
            <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700, color: 'var(--accent)', background: 'rgba(99,102,241,0.12)', borderRadius: 4, padding: '1px 5px' }}>
              {a.token_outcome}
            </span>
          )}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>🤖 {a.model}</div>
        <Badge color={color}>{verdictLabel(a)}</Badge>
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
        <Mini label="Рынок" value={pct(a.market_prob)} />
        <Mini label="Модель" value={pct(a.my_prob)} />
        <Mini label="Edge" value={`${a.edge >= 0 ? '+' : ''}${(a.edge * 100).toFixed(1)}%`} color={a.edge >= 0 ? 'var(--green)' : 'var(--red)'} />
      </div>
      {a.reasoning && (
        <div style={{ marginTop: 10, padding: '8px 10px', background: 'rgba(99,102,241,0.08)', borderRadius: 8, fontSize: 12, color: 'var(--muted)', lineHeight: 1.5, borderLeft: '3px solid var(--accent)' }}>
          {a.reasoning}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>{new Date(a.created_at).toLocaleString('ru')}</div>
    </div>
  )
}

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={card}>
      <div style={{ fontSize: 10.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 19, fontWeight: 800, marginTop: 4, color: color ?? 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Mini({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2, color: color ?? 'var(--text)' }}>{value}</div>
    </div>
  )
}

function pct(v: number | null) { return v === null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtSigned(n: number) {
  if (n > 0) return `+$${n.toFixed(2)}`
  if (n < 0) return `-$${Math.abs(n).toFixed(2)}`
  return '$0.00'
}
function pnlColor(n: number) { return n >= 0 ? 'var(--green)' : 'var(--red)' }
