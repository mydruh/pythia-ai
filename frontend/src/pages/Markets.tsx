import { useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { api, type Market } from '../api/client'
import { Screen, Header, Loader, Empty, Button, card } from '../components/ui'
import CategoryChips from '../components/CategoryChips'

const PAGE = 20

export default function Markets() {
  const [category, setCategory] = useState<string | null>(null)
  const [sort, setSort] = useState<'volume' | 'closing'>('volume')
  const [page, setPage] = useState(0)

  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => api.categories() })
  const { data, isLoading, error } = useQuery({
    queryKey: ['markets', category, sort, page],
    queryFn: () => api.markets({ category, sort, limit: PAGE, offset: page * PAGE }),
    placeholderData: keepPreviousData,
  })

  const markets = data?.data ?? []
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  function changeCategory(c: string | null) { setCategory(c); setPage(0) }
  function changeSort(srt: 'volume' | 'closing') { setSort(srt); setPage(0) }

  return (
    <Screen>
      <Header title="Лоты Polymarket" subtitle={`${total} рынков`} />
      <CategoryChips categories={categories} value={category} onChange={changeCategory} />

      <div style={{ display: 'flex', gap: 8, padding: '0 16px 12px' }}>
        <SortBtn active={sort === 'volume'} onClick={() => changeSort('volume')}>По объёму</SortBtn>
        <SortBtn active={sort === 'closing'} onClick={() => changeSort('closing')}>По закрытию</SortBtn>
      </div>

      <div style={{ padding: '0 16px' }}>
        {isLoading ? <Loader />
          : error ? <Empty>⚠️ Не удалось загрузить лоты.</Empty>
          : markets.length === 0 ? <Empty>{'Лотов нет.\nЗапусти цикл, чтобы подтянуть рынки.'}</Empty>
          : markets.map(m => <MarketCard key={m.id} m={m} />)}
      </div>

      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '8px 16px' }}>
          <Button variant="ghost" disabled={page === 0} onClick={() => setPage(p => p - 1)} style={{ width: 'auto', padding: '10px 18px' }}>←</Button>
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>{page + 1} / {pages}</span>
          <Button variant="ghost" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)} style={{ width: 'auto', padding: '10px 18px' }}>→</Button>
        </div>
      )}
    </Screen>
  )
}

function SortBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '7px 14px', borderRadius: 10, cursor: 'pointer', fontSize: 13, fontWeight: 600,
      border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
      background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
      color: active ? 'var(--text)' : 'var(--muted)',
    }}>{children}</button>
  )
}

function fmtVolume(v: number | null): string {
  if (v === null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function MarketCard({ m }: { m: Market }) {
  return (
    <div style={{ ...card, marginBottom: 10 }}>
      <div style={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.4 }}>
        {m.url
          ? <a href={m.url} target="_blank" rel="noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>{m.question}</a>
          : m.question}
      </div>
      <div style={{ display: 'flex', gap: 14, marginTop: 10, fontSize: 12.5, color: 'var(--muted)' }}>
        <span>📊 {fmtVolume(m.volume)}</span>
        {m.close_time && <span>⏳ {new Date(m.close_time).toLocaleDateString('ru')}</span>}
      </div>
    </div>
  )
}
