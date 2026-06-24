import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { getTelegramId } from '../hooks/useUser'
import { Screen, Header, Button, card } from '../components/ui'

export default function Account() {
  const uid = getTelegramId()!
  const qc = useQueryClient()
  const [amount, setAmount] = useState('1000')

  const { data: wallet } = useQuery({ queryKey: ['wallet', uid], queryFn: () => api.walletStats(uid) })

  // Гарантируем, что кошелёк существует (идемпотентно).
  const ensureMut = useMutation({
    mutationFn: () => api.walletStart({ telegram_id: uid }),
  })
  const depositMut = useMutation({
    mutationFn: async () => {
      await ensureMut.mutateAsync()
      return api.deposit(uid, Number(amount))
    },
    onSuccess: () => qc.invalidateQueries(),
  })

  const valid = Number(amount) > 0

  return (
    <Screen>
      <Header title="Аккаунт" subtitle="Кошелёк и пополнение" />

      <div style={{ padding: 16 }}>
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4 }}>Свободные средства</div>
          <div style={{ fontSize: 26, fontWeight: 800, marginTop: 4 }}>
            ${(wallet?.free_balance ?? 0).toFixed(2)}
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
            Всего внесено: ${(wallet?.total_deposited ?? 0).toFixed(2)} · В ботах: ${(wallet?.bots_balance ?? 0).toFixed(2)}
          </div>
        </div>

        <div style={card}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>💰 Пополнить кошелёк</div>
          <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>Сумма ($)</div>
          <input
            style={{ width: '100%', padding: '11px 12px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 15, outline: 'none', marginBottom: 12 }}
            type="number" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)}
          />
          <Button disabled={!valid || depositMut.isPending} onClick={() => depositMut.mutate()}>
            {depositMut.isPending ? 'Пополнение...' : 'Пополнить'}
          </Button>
          {depositMut.isSuccess && <div style={{ fontSize: 13, color: 'var(--green)', marginTop: 10, fontWeight: 600 }}>✅ Кошелёк пополнен</div>}
          {depositMut.isError && <div style={{ fontSize: 13, color: 'var(--red)', marginTop: 10, fontWeight: 600 }}>⚠️ Ошибка пополнения</div>}
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 12, lineHeight: 1.5 }}>
            Это внесённый капитал (не прибыль) — доходность не искажается. Из свободных средств выделяется бюджет ботов.
          </div>
        </div>
      </div>
    </Screen>
  )
}
