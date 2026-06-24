/** Разрешён ли запуск дашборда в обычном браузере (без Telegram). */
export const ALLOW_BROWSER = import.meta.env.VITE_ALLOW_BROWSER === 'true'

/** uid по умолчанию для standalone-браузера (локальная разработка). */
const DEV_UID = Number(import.meta.env.VITE_DEV_UID ?? 1)

/** Получить telegram_id из URL-параметра ?uid=..., Telegram WebApp SDK,
 *  либо — если разрешён браузерный режим — из дефолтного DEV_UID. */
export function getTelegramId(): number | null {
  const params = new URLSearchParams(window.location.search)
  const fromUrl = params.get('uid')
  if (fromUrl) return parseInt(fromUrl, 10)

  const tg = (window as any).Telegram?.WebApp
  const id = tg?.initDataUnsafe?.user?.id
  if (id) return id

  if (ALLOW_BROWSER) return DEV_UID
  return null
}
