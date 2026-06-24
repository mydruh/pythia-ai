import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, Tooltip, ReferenceLine,
} from 'recharts'
import type { PnlPoint } from '../api/client'

interface Props { data: PnlPoint[] }

export default function PnlChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '32px 16px', fontSize: 14 }}>
        График появится после первых закрытых позиций
      </div>
    )
  }

  const min = Math.min(...data.map(d => d.cumulative_pnl))
  const max = Math.max(...data.map(d => d.cumulative_pnl))
  const color = (data.at(-1)?.cumulative_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444'

  const chartData = data.map(d => ({
    ...d,
    date: new Date(d.closed_at).toLocaleDateString('ru', { month: 'short', day: 'numeric' }),
  }))

  return (
    <div style={{ padding: '0 16px 16px' }}>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>Накопленный P&L (USDC)</div>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="pnl" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.3} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
          <YAxis domain={[Math.min(min, 0) * 1.1, max * 1.1]} tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: '#1a1d2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }}
            formatter={(v: number) => [`${v >= 0 ? '+' : ''}${v.toFixed(2)} USDC`, 'P&L']}
          />
          <ReferenceLine y={0} stroke="#2a2d3e" strokeDasharray="4 4" />
          <Area type="monotone" dataKey="cumulative_pnl" stroke={color} fill="url(#pnl)" strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
