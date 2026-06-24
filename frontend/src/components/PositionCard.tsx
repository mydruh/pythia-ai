import type { Position } from '../api/client'

const s: Record<string, React.CSSProperties> = {
  card: {
    background: 'var(--card)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    padding: '14px 16px',
    marginBottom: 10,
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
  question: { fontSize: 13, fontWeight: 600, flex: 1, lineHeight: 1.4 },
  side: {
    padding: '2px 8px',
    borderRadius: 6,
    fontSize: 11,
    fontWeight: 700,
    flexShrink: 0,
  },
  row: { display: 'flex', justifyContent: 'space-between', marginTop: 10, gap: 8 },
  metric: { flex: 1 },
  metricLabel: { fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase' },
  metricValue: { fontSize: 14, fontWeight: 600, marginTop: 2 },
  reasoning: {
    marginTop: 10,
    padding: '8px 10px',
    background: 'rgba(99,102,241,0.08)',
    borderRadius: 8,
    fontSize: 12,
    color: 'var(--muted)',
    lineHeight: 1.5,
    borderLeft: '3px solid var(--accent)',
  },
}

function pnlColor(v: number | null) {
  if (v === null) return 'var(--text)'
  return v >= 0 ? 'var(--green)' : 'var(--red)'
}
function fmt(v: number | null, sign = false) {
  if (v === null) return '—'
  if (sign) {
    if (v > 0) return `+$${v.toFixed(2)}`
    if (v < 0) return `-$${Math.abs(v).toFixed(2)}`
    return '$0.00'
  }
  return `$${v.toFixed(2)}`
}

function resultBadge(pos: Position): { label: string; color: string } | null {
  if (pos.status !== 'closed' || pos.pnl === null) return null
  if (pos.pnl > 0) return { label: `✓ Выиграно`, color: '#22c55e' }
  if (pos.pnl < 0) return { label: `✗ Проиграно`, color: '#ef4444' }
  return { label: '= Ноль', color: '#94a3b8' }
}

export default function PositionCard({ pos }: { pos: Position }) {
  const sideColor = pos.side === 'YES' ? '#22c55e' : '#ef4444'
  const pnl = pos.status === 'open' ? pos.unrealized_pnl : pos.pnl
  // Акций куплено = size / entry_price. При выигрыше = $1 за акцию → прибыль = size/entry_price − size
  const potentialProfit = pos.entry_price > 0 ? pos.size_usdc / pos.entry_price - pos.size_usdc : null
  const result = resultBadge(pos)

  const cardStyle: React.CSSProperties = result
    ? { ...s.card, borderLeft: `3px solid ${result.color}` }
    : s.card

  return (
    <div style={cardStyle}>
      <div style={s.header}>
        <div style={s.question}>
          {pos.market_url
            ? <a href={pos.market_url} target="_blank" rel="noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>{pos.market_question}</a>
            : pos.market_question}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          {result && (
            <span style={{ fontSize: 11, fontWeight: 700, color: result.color }}>{result.label}</span>
          )}
          <div style={{ ...s.side, background: sideColor + '22', color: sideColor }}>
            {pos.side}{pos.token_outcome ? ` · ${pos.token_outcome}` : ''}
          </div>
        </div>
      </div>

      <div style={s.row}>
        <div style={s.metric}>
          <div style={s.metricLabel}>Размер</div>
          <div style={s.metricValue}>{fmt(pos.size_usdc)}</div>
        </div>
        <div style={s.metric}>
          <div style={s.metricLabel}>Вход</div>
          <div style={s.metricValue}>{(pos.entry_price * 100).toFixed(1)}%</div>
        </div>
        {pos.current_price !== null && (
          <div style={s.metric}>
            <div style={s.metricLabel}>Сейчас</div>
            <div style={s.metricValue}>{(pos.current_price * 100).toFixed(1)}%</div>
          </div>
        )}
        <div style={s.metric}>
          <div style={s.metricLabel}>{pos.status === 'open' ? 'Нереализ.' : 'P&L'}</div>
          <div style={{ ...s.metricValue, color: pnlColor(pnl) }}>{fmt(pnl, true)}</div>
        </div>
      </div>

      {pos.status === 'open' && potentialProfit !== null && (
        <div style={{
          marginTop: 8,
          padding: '6px 10px',
          background: 'rgba(34,197,94,0.08)',
          borderRadius: 8,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>При выигрыше</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--green)' }}>
            +${potentialProfit.toFixed(2)}
          </span>
        </div>
      )}

      {pos.reasoning && (
        <div style={s.reasoning}>🤖 {pos.reasoning}</div>
      )}

      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>
        {new Date(pos.opened_at).toLocaleString('ru')}
        {pos.closed_at && ` → ${new Date(pos.closed_at).toLocaleString('ru')}`}
      </div>
    </div>
  )
}
