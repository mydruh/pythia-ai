"""Аутентификация запросов к API.

Два легитимных источника вызовов:
  1. Telegram Mini App (браузер пользователя) — присылает подписанный initData.
     Проверяем HMAC-подпись токеном бота и достаём telegram_id ИЗ подписи.
  2. Сервисы (Telegram-бот) — ходят server-to-server, initData у них нет.
     Аутентифицируются общим секретом X-Internal-Token.

Для локальной разработки в браузере без Telegram есть флаг
settings.allow_unverified_auth — он разрешает доверять telegram_id из пути.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Path

from config import settings


class TelegramAuthError(Exception):
    """initData не прошёл проверку подписи."""


def verify_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86_400) -> dict:
    """Проверить подпись Telegram WebApp initData и вернуть объект user.

    Алгоритм (docs Telegram «Validating data received via the Mini App»):
      secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
      hash       = HMAC_SHA256(key=secret_key, msg=data_check_string)
    где data_check_string — поля (кроме hash), отсортированные по ключу,
    в формате key=value, соединённые '\\n'.
    """
    if not init_data:
        raise TelegramAuthError("пустой initData")

    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as e:
        raise TelegramAuthError("некорректный initData") from e

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("в initData нет hash")

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise TelegramAuthError("подпись не совпала")

    # Защита от replay: initData не должен быть слишком старым.
    auth_date = parsed.get("auth_date")
    if auth_date is not None:
        try:
            age = time.time() - int(auth_date)
        except ValueError as e:
            raise TelegramAuthError("некорректный auth_date") from e
        if age > max_age_seconds:
            raise TelegramAuthError("initData просрочен")

    user_raw = parsed.get("user")
    if not user_raw:
        raise TelegramAuthError("в initData нет user")
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError as e:
        raise TelegramAuthError("некорректный user в initData") from e


async def require_user(
    telegram_id: int = Path(...),
    x_telegram_init_data: str | None = Header(default=None),
    x_internal_token: str | None = Header(default=None),
) -> int:
    """Зависимость FastAPI: подтвердить право вызывающего на ресурс telegram_id.

    Приоритет проверки:
      1. initData (Mini App) -> проверяем подпись, сверяем id с путём.
      2. X-Internal-Token (бот/сервисы) -> доверяем (server-to-server).
      3. allow_unverified_auth (dev) -> доверяем telegram_id из пути.
      4. иначе -> 401.
    """
    # 1. Mini App: подписанный initData.
    if x_telegram_init_data:
        if not settings.telegram_bot_token:
            raise HTTPException(500, "TELEGRAM_BOT_TOKEN не задан на бэкенде")
        try:
            user = verify_init_data(x_telegram_init_data, settings.telegram_bot_token)
        except TelegramAuthError:
            raise HTTPException(401, "Невалидный Telegram initData")
        verified_id = int(user.get("id", 0))
        if verified_id != telegram_id:
            raise HTTPException(403, "Доступ только к своим данным")
        return verified_id

    # 2. Доверенный сервис (бот) по общему секрету.
    token = settings.internal_api_token
    if token and x_internal_token and hmac.compare_digest(x_internal_token, token):
        return telegram_id

    # 3. Режим разработки: без проверки (браузер без Telegram).
    if settings.allow_unverified_auth:
        return telegram_id

    # 4. Иначе — отказ.
    raise HTTPException(401, "Требуется Telegram-авторизация")
