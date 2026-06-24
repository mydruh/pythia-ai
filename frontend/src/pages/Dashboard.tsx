import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { getTelegramId } from '../hooks/useUser'
import PnlChart from '../components/PnlChart'
import { Screen, Loader, Empty, card } from '../components/ui'

export default function Dashboard({ onOpenBots }: { onOpenBots: () => void }) {
  const uid = getTelegramId()!
  const { data: stats, isLoading, error } = useQuery({ queryKey: ['wallet', uid], queryFn: () => api.walletStats(uid) })
  const { data: pnl = [] } = useQuery({ queryKey: ['userPnl', uid], queryFn: () => api.userPnl(uid) })

  if (isLoading) return <Loader />
  if (error || !stats) return (
    <Empty>{'⚠️ Кошелёк не настроен.\nОткрой вкладку «Аккаунт» и внеси депозит.'}</Empty>
  )

  const retColor = stats.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)'

  return (
    <Screen>
      {/* Hero-баланс */}
      <div style={{
        margin: '16px 16px 6px', padding: 20, borderRadius: 20,
        background: 'linear-gradient(135deg, rgba(99,102,241,0.25), rgba(129,140,248,0.08))',
        border: '1px solid var(--border)',
      }}>
        <div style={{ fontSize: 12, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Капитал (equity)
        </div>
        <div style={{ fontSize: 34, fontWeight: 800, marginTop: 4 }}>
          ${stats.equity.toFixed(2)}
        </div>
        <div style={{ fontSize: 14, fontWeight: 700, color: retColor, marginTop: 2 }}>
          {stats.total_return_pct >= 0 ? '+' : ''}{stats.total_return_pct.toFixed(2)}% от внесённого
        </div>
      </div>

      {/* Сетка метрик */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '10px 16px' }}>
        <Tile label="Свободно" value={`$${stats.free_balance.toFixed(2)}`} sub="не в ботах" />
        <Tile label="В ботах" value={`$${stats.bots_balance.toFixed(2)}`} sub={`${stats.active_sessions} активных`} onClick={onOpenBots} />
        <Tile label="Реализованный P&L" value={fmtSigned(stats.realized_pnl)} color={pnlColor(stats.realized_pnl)} />
        <Tile label="Нереализованный" value={fmtSigned(stats.unrealized_pnl)} color={pnlColor(stats.unrealized_pnl)} />
      </div>

      <div style={{ padding: '6px 16px' }}>
        <div style={{ fontSize: 12, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>
          График P&L
        </div>
        {pnl.length > 0 ? <PnlChart data={pnl} /> : <Empty>Пока нет закрытых сделок.</Empty>}
      </div>
    </Screen>
  )
}

function Tile({ label, value, sub, color, onClick }: {
  label: string; value: string; sub?: string; color?: string; onClick?: () => void
}) {
  return (
    <div style={{ ...card, cursor: onClick ? 'pointer' : 'default' }} onClick={onClick}>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4, color: color ?? 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function fmtSigned(n: number) {
  if (n > 0) return `+$${n.toFixed(2)}`
  if (n < 0) return `-$${Math.abs(n).toFixed(2)}`
  return '$0.00'
}
function pnlColor(n: number) { return n >= 0 ? 'var(--green)' : 'var(--red)' }
