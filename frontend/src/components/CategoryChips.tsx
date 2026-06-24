import { catLabel, type Category } from '../api/client'

/** Горизонтальная лента чипов-фильтров категорий.
 *  value=null → «Все». Опции — из /categories. */
export default function CategoryChips({ categories, value, onChange, allLabel = 'Все' }: {
  categories: Category[]
  value: string | null
  onChange: (cat: string | null) => void
  allLabel?: string
}) {
  const chips: { key: string | null; label: string; count?: number }[] = [
    { key: null, label: allLabel },
    ...categories.map(c => ({ key: c.category, label: catLabel(c.category), count: c.count })),
  ]
  return (
    <div style={{
      display: 'flex', gap: 8, overflowX: 'auto', padding: '4px 16px 12px',
      scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch',
    }}>
      {chips.map(ch => {
        const on = value === ch.key
        return (
          <button key={ch.key ?? '__all'} onClick={() => onChange(ch.key)} style={{
            flexShrink: 0, padding: '7px 14px', borderRadius: 999, cursor: 'pointer',
            fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap',
            border: `1px solid ${on ? 'transparent' : 'var(--border)'}`,
            background: on ? 'linear-gradient(135deg, var(--accent), #818cf8)' : 'var(--card)',
            color: on ? '#fff' : 'var(--muted)',
          }}>
            {ch.label}{ch.count !== undefined ? ` · ${ch.count}` : ''}
          </button>
        )
      })}
    </div>
  )
}
