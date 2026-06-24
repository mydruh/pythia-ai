import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, catLabel } from '../api/client'
import { getTelegramId } from '../hooks/useUser'
import { Screen, Header, Loader, Empty, Button } from '../components/ui'
import Modal from '../components/Modal'
import BotCard from '../components/BotCard'

const MAX_ACTIVE = 3

export default function Bots({ onOpenBot }: { onOpenBot: (sid: number) => void }) {
  const uid = getTelegramId()!
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)

  const { data: bots = [], isLoading } = useQuery({ queryKey: ['bots', uid], queryFn: () => api.listBots(uid) })
  const { data: wallet } = useQuery({ queryKey: ['wallet', uid], queryFn: () => api.walletStats(uid) })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => api.categories() })

  const activeCount = bots.filter(b => b.status === 'active').length
  const canLaunch = activeCount < MAX_ACTIVE

  return (
    <Screen>
      <Header title="Автоторговля" subtitle={`Активных ботов: ${activeCount} / ${MAX_ACTIVE}`} />

      <div style={{ padding: '0 16px 12px' }}>
        <Button disabled={!canLaunch} onClick={() => setOpen(true)}>
          {canLaunch ? '＋ Запустить автоторговлю' : `Достигнут лимит (${MAX_ACTIVE})`}
        </Button>
        {wallet && (
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8, textAlign: 'center' }}>
            Свободно для бюджета: <b style={{ color: 'var(--text)' }}>${wallet.free_balance.toFixed(2)}</b>
          </div>
        )}
      </div>

      <div style={{ padding: '0 16px' }}>
        {isLoading ? <Loader />
          : bots.length === 0 ? <Empty>{'Ботов пока нет.\nЗапусти первую автоторговлю — по категории или по всем рынкам.'}</Empty>
          : bots.map(b => <BotCard key={b.id} bot={b} onClick={() => onOpenBot(b.id)} />)}
      </div>

      <LaunchModal
        open={open}
        onClose={() => setOpen(false)}
        uid={uid}
        freeBalance={wallet?.free_balance ?? 0}
        categories={categories.map(c => c.category).filter(c => c !== 'Прочее')}
        onCreated={() => { qc.invalidateQueries(); setOpen(false) }}
      />
    </Screen>
  )
}

function LaunchModal({ open, onClose, uid, freeBalance, categories, onCreated }: {
  open: boolean
  onClose: () => void
  uid: number
  freeBalance: number
  categories: string[]
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [budget, setBudget] = useState('300')
  const [days, setDays] = useState('30')

  const mut = useMutation({
    mutationFn: () => api.createBot(uid, {
      name: name.trim() || null,
      category,
      budget: Number(budget),
      days: Number(days),
    }),
    onSuccess: onCreated,
  })

  const budgetNum = Number(budget)
  const valid = budgetNum > 0 && budgetNum <= freeBalance && Number(days) > 0

  return (
    <Modal open={open} onClose={onClose} title="Новая автоторговля">
      <Field label="Название (необязательно)">
        <input style={inp} value={name} onChange={e => setName(e.target.value)} placeholder="Напр. Крипто-эксперимент" />
      </Field>

      <Field label="Категория">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Chip active={category === null} onClick={() => setCategory(null)}>Все категории</Chip>
          {categories.map(c => (
            <Chip key={c} active={category === c} onClick={() => setCategory(c)}>{catLabel(c)}</Chip>
          ))}
        </div>
      </Field>

      <Field label={`Бюджет $ (свободно $${freeBalance.toFixed(2)})`}>
        <input style={inp} type="number" inputMode="decimal" value={budget} onChange={e => setBudget(e.target.value)} />
        {budgetNum > freeBalance && <Hint color="var(--red)">Недостаточно свободных средств</Hint>}
      </Field>

      <Field label="Период, дней">
        <input style={inp} type="number" inputMode="numeric" value={days} onChange={e => setDays(e.target.value)} />
      </Field>

      <Button disabled={!valid || mut.isPending} onClick={() => mut.mutate()}>
        {mut.isPending ? 'Запуск...' : 'Запустить'}
      </Button>
      {mut.isError && <Hint color="var(--red)">⚠️ {(mut.error as Error).message}</Hint>}
    </Modal>
  )
}

const inp: React.CSSProperties = {
  width: '100%', padding: '11px 12px', borderRadius: 10, border: '1px solid var(--border)',
  background: 'var(--bg)', color: 'var(--text)', fontSize: 15, outline: 'none',
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '7px 13px', borderRadius: 999, cursor: 'pointer', fontSize: 13, fontWeight: 600,
      border: `1px solid ${active ? 'transparent' : 'var(--border)'}`,
      background: active ? 'linear-gradient(135deg, var(--accent), #818cf8)' : 'var(--card)',
      color: active ? '#fff' : 'var(--muted)',
    }}>{children}</button>
  )
}

function Hint({ children, color }: { children: React.ReactNode; color?: string }) {
  return <div style={{ fontSize: 12, color: color ?? 'var(--muted)', marginTop: 8 }}>{children}</div>
}
