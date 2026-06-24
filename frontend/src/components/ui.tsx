import type { CSSProperties, ReactNode } from 'react'

/** Общие визуальные примитивы (единый рестайл-стиль). */

export const card: CSSProperties = {
  background: 'var(--card)',
  border: '1px solid var(--border)',
  borderRadius: 16,
  padding: 16,
  boxShadow: '0 1px 2px rgba(0,0,0,0.25)',
}

export function Screen({ children }: { children: ReactNode }) {
  return <div style={{ paddingBottom: 88 }}>{children}</div>
}

export function Header({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '18px 16px 10px',
    }}>
      <div>
        <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: -0.5 }}>{title}</div>
        {subtitle && <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>{subtitle}</div>}
      </div>
      {right}
    </div>
  )
}

export function Button({ children, onClick, disabled, variant = 'primary', style }: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: 'primary' | 'ghost' | 'danger'
  style?: CSSProperties
}) {
  const base: CSSProperties = {
    width: '100%', padding: '13px 0', borderRadius: 12, border: 'none',
    fontWeight: 700, fontSize: 14.5, cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1, transition: 'opacity .15s',
  }
  const variants: Record<string, CSSProperties> = {
    primary: { background: 'linear-gradient(135deg, var(--accent), #818cf8)', color: '#fff' },
    ghost: { background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' },
    danger: { background: 'rgba(239,68,68,0.12)', color: 'var(--red)', border: '1px solid rgba(239,68,68,0.3)' },
  }
  return (
    <button style={{ ...base, ...variants[variant], ...style }} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  )
}

export function Loader({ text = 'Загрузка...' }: { text?: string }) {
  return <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '64px 16px', fontSize: 14 }}>{text}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '40px 16px', fontSize: 14, whiteSpace: 'pre-line' }}>
      {children}
    </div>
  )
}

export function Badge({ children, color }: { children: ReactNode; color: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '3px 9px', borderRadius: 8,
      fontSize: 11, fontWeight: 700, background: color + '22', color,
    }}>
      {children}
    </span>
  )
}

export const STATUS_META: Record<string, [string, string]> = {
  active: ['🟢 Активна', '#22c55e'],
  paused: ['⏸ Пауза', '#eab308'],
  settling: ['⌛ Ожидание результатов', '#38bdf8'],
  completed: ['✅ Завершена', '#6366f1'],
  stopped: ['⏹ Остановлена', '#94a3b8'],
}
