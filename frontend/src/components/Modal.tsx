import type { ReactNode } from 'react'

/** Нижний bottom-sheet (как в мобильных приложениях). */
export default function Modal({ open, onClose, title, children }: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  if (!open) return null
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center', zIndex: 200,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 520, background: 'var(--card)',
          borderTopLeftRadius: 22, borderTopRightRadius: 22,
          border: '1px solid var(--border)', borderBottom: 'none',
          padding: '8px 16px calc(20px + env(safe-area-inset-bottom, 0px))',
          maxHeight: '88vh', overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'center', padding: '6px 0 14px' }}>
          <div style={{ width: 40, height: 4, borderRadius: 2, background: 'var(--border)' }} />
        </div>
        <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 14 }}>{title}</div>
        {children}
      </div>
    </div>
  )
}
