export type TabKey = 'dashboard' | 'markets' | 'bots' | 'account'

export const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'dashboard', label: 'Кошелёк', icon: '◎' },
  { key: 'markets', label: 'Лоты', icon: '▦' },
  { key: 'bots', label: 'Боты', icon: '⚡' },
  { key: 'account', label: 'Аккаунт', icon: '⚙' },
]

export default function TabBar({ active, onChange }: { active: TabKey; onChange: (k: TabKey) => void }) {
  return (
    <nav style={{
      position: 'fixed', left: 12, right: 12, bottom: 12,
      display: 'flex', gap: 4, padding: 6,
      background: 'rgba(26,29,46,0.92)', backdropFilter: 'blur(12px)',
      border: '1px solid var(--border)', borderRadius: 18,
      boxShadow: '0 8px 24px rgba(0,0,0,0.4)', zIndex: 100,
      paddingBottom: `calc(6px + env(safe-area-inset-bottom, 0px))`,
    }}>
      {TABS.map(t => {
        const on = active === t.key
        return (
          <button key={t.key} onClick={() => onChange(t.key)} style={{
            flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
            padding: '8px 0', borderRadius: 13, border: 'none', cursor: 'pointer',
            background: on ? 'linear-gradient(135deg, var(--accent), #818cf8)' : 'transparent',
            color: on ? '#fff' : 'var(--muted)', transition: 'background .2s',
          }}>
            <span style={{ fontSize: 17, lineHeight: 1 }}>{t.icon}</span>
            <span style={{ fontSize: 10.5, fontWeight: 700 }}>{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
