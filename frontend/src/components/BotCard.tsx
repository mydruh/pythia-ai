import { catLabel, type Bot } from '../api/client'
import { card, Badge, STATUS_META } from './ui'

export default function BotCard({ bot, onClick }: { bot: Bot; onClick: () => void }) {
  const [statusLabel, statusColor] = STATUS_META[bot.status] ?? [bot.status, '#94a3b8']
  const ret = bot.total_return_pct
  const retColor = ret >= 0 ? 'var(--green)' : 'var(--red)'

  return (
    <div onClick={onClick} style={{ ...card, marginBottom: 10, cursor: 'pointer' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>
          {bot.name || catLabel(bot.category)}
        </div>
        <Badge color={statusColor}>{statusLabel}</Badge>
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
        {catLabel(bot.category)}
      </div>

      <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
        <Metric label="Капитал" value={`$${bot.equity.toFixed(0)}`} sub={`из $${bot.starting_balance.toFixed(0)}`} />
        <Metric label="Доходность" value={`${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`} color={retColor} />
        <Metric label="Позиции" value={`${bot.open_positions + bot.closed_positions}`} sub={`${bot.closed_positions} закр.`} />
      </div>
    </div>
  )
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, marginTop: 2, color: color ?? 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{sub}</div>}
    </div>
  )
}
