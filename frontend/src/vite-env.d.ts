/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Базовый URL API. По умолчанию '/api' (через vite-прокси). */
  readonly VITE_API_URL?: string
  /** 'true' — разрешить запуск дашборда в обычном браузере без Telegram. */
  readonly VITE_ALLOW_BROWSER?: string
  /** uid по умолчанию для браузерного режима (локальная разработка). */
  readonly VITE_DEV_UID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
