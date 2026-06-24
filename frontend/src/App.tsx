import { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import Markets from './pages/Markets'
import Bots from './pages/Bots'
import BotDetail from './pages/BotDetail'
import Account from './pages/Account'
import TabBar, { type TabKey } from './components/TabBar'
import { getTelegramId, ALLOW_BROWSER } from './hooks/useUser'

function NoUser() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: '100vh', padding: 24, textAlign: 'center',
      color: 'var(--muted)', gap: 12,
    }}>
      <div style={{ fontSize: 32 }}>🤖</div>
      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>Pythia</div>
      <div style={{ fontSize: 13 }}>
        {ALLOW_BROWSER ? (
          <>Укажите пользователя в URL: <code>?uid=1</code></>
        ) : (
          <>Откройте дашборд через Telegram-бот.<br />
            Нажмите кнопку «Открыть дашборд» в боте.</>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<TabKey>('dashboard')
  const [botId, setBotId] = useState<number | null>(null)

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    if (tg) { tg.ready(); tg.expand() }
  }, [])

  const uid = getTelegramId()
  if (!uid) return <NoUser />

  // Детальная страница бота — поверх вкладки «Боты», со своей кнопкой «Назад».
  if (botId !== null) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg)' }}>
        <BotDetail sid={botId} onBack={() => setBotId(null)} />
        <TabBar active="bots" onChange={(t) => { setBotId(null); setTab(t) }} />
      </div>
    )
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)' }}>
      {tab === 'dashboard' && <Dashboard onOpenBots={() => setTab('bots')} />}
      {tab === 'markets' && <Markets />}
      {tab === 'bots' && <Bots onOpenBot={(sid) => setBotId(sid)} />}
      {tab === 'account' && <Account />}
      <TabBar active={tab} onChange={setTab} />
    </div>
  )
}
