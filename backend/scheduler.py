"""Торговый цикл (APScheduler). Per-session: у каждой автоторговли свой бюджет.

Цикл:
  1. Fetch новых лотов из Gamma API.
  2. Для каждой активной автоторговли: фильтр → анализ → риск → сделка
     (в рамках своей категории и бюджета).
  3. Обновляет цены открытых позиций.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.analyzer import Analyzer
from core.executor import Executor
from core.fetcher import fetch_market_winner, fetch_token_quote, fetch_token_resolution, run_fetch
from core.notifier import notify
from core.risk_manager import DrawdownHalt, RiskManager
from db.models import (
    Analysis, AppState, Market, MarketStatus, MarketToken,
    Position, PositionStatus, Resolution, SessionStatus, TradingSession, User, Verdict,
)
from db.session import async_session_factory

logger = logging.getLogger(__name__)

MAX_FINALISTS = 5
MAX_FILTER_ATTEMPTS = 10   # топ-10 рынков по объёму → ≤5 финалистов. Меньше = меньше rate-limit давления.


class TradingCycle:
    def __init__(self) -> None:
        self.analyzer = Analyzer()
        self.executor = Executor()
        self._running = False
        self._needs_extra_run = False  # запрос доп. цикла, пока текущий ещё идёт
        self._sl_running = False       # идёт ли фоновая проверка стоп-лосса (защита от наложения)

    async def run_once(self) -> None:
        if self._running:
            self._needs_extra_run = True
            logger.info("Цикл уже выполняется — будет запущен повторно после завершения")
            return
        self._running = True
        try:
            await self._run_impl()
        finally:
            self._running = False

        # Если во время цикла появились новые боты — запустить ещё раз через create_task,
        # НЕ рекурсивно (рекурсия внутри APScheduler-managed корутины ломает его loop).
        if self._needs_extra_run:
            self._needs_extra_run = False
            logger.info("Запускаем дополнительный цикл для новых сессий")
            asyncio.create_task(self._run_impl_safe())

    async def _run_impl_safe(self) -> None:
        """Обёртка для фонового запуска через create_task — не ломает APScheduler loop."""
        if self._running:
            return
        self._running = True
        try:
            await self._run_impl()
        except Exception:
            logger.exception("Ошибка в дополнительном цикле")
        finally:
            self._running = False

    async def run_stop_loss_check(self) -> None:
        """Частая лёгкая проверка стоп-лосса и урегулирования — без LLM.

        За один тик делает два дела:
          1. Стоп-лосс: тянет CLOB-стакан, режет позицию при убытке ≥ порога.
          2. Урегулирование: для позиций на истёкших рынках спрашивает Gamma (closed=true)
             и закрывает по реальному исходу. Это позволяет не ждать 30 мин цикла после
             публикации резолюции Polymarket.

        Пропускаем тик, если идёт основной цикл (он сам трогает позиции) или
        предыдущая проверка ещё не закончилась — чтобы не закрыть позицию дважды."""
        if self._running or self._sl_running:
            return
        self._sl_running = True
        try:
            async with async_session_factory() as session:
                now = datetime.now(timezone.utc)

                # 1. Стоп-лосс: только торгуемые рынки (close_time > now).
                if settings.early_exit_enabled:
                    open_positions = await session.scalars(
                        select(Position)
                        .join(TradingSession, TradingSession.id == Position.session_id)
                        .join(Market, Market.id == Position.market_id)
                        .where(
                            Position.status == PositionStatus.open,
                            TradingSession.status.in_([SessionStatus.active, SessionStatus.settling]),
                            Market.status == MarketStatus.open,
                            Market.close_time > now,
                        )
                    )
                    for pos in open_positions.all():
                        quote = await fetch_token_quote(pos.token_id)
                        if quote is None:
                            continue
                        bid, ask = quote
                        if bid is None:
                            continue
                        shares = pos.size_usdc / pos.entry_price if pos.entry_price else 0.0
                        unrealized = (bid - pos.entry_price) * shares
                        pos.current_price = (bid + ask) / 2.0 if ask is not None else bid
                        await session.commit()
                        if unrealized <= -settings.early_exit_stop_loss_pct * pos.size_usdc:
                            await self.executor.close_position(session, pos, bid)
                            logger.info(
                                "STOP-LOSS(fast) session=%d pos=%d side=%s exit@%.3f pnl=%.2f (-%.0f%%)",
                                pos.session_id, pos.id, pos.side, bid, unrealized,
                                settings.early_exit_stop_loss_pct * 100,
                            )
                            sl_ts = await session.get(TradingSession, pos.session_id)
                            sl_user = await session.get(User, sl_ts.user_id) if sl_ts else None
                            if sl_user:
                                market_obj = await session.get(Market, pos.market_id)
                                pnl = pos.pnl or 0.0
                                notify(sl_user.telegram_id,
                                    f"🔴 Стоп-лосс сработал\n"
                                    f"{(market_obj.question[:60] if market_obj else '—')}\n\n"
                                    f"P&L: -${abs(pnl):.2f} "
                                    f"(-{settings.early_exit_stop_loss_pct:.0%} от ставки)"
                                )

                # 2. Урегулирование истёкших рынков (не зависит от early_exit_enabled).
                sessions_list = (await session.scalars(
                    select(TradingSession).where(
                        TradingSession.status.in_([
                            SessionStatus.active, SessionStatus.paused, SessionStatus.settling,
                        ])
                    )
                )).all()

                for ts in sessions_list:
                    try:
                        u = await session.get(User, ts.user_id)
                        await _settle_resolved_positions(
                            session, ts.id, self.executor,
                            telegram_id=u.telegram_id if u else None,
                        )
                    except Exception:
                        logger.exception("Ошибка при фоновом урегулировании session=%d", ts.id)

                # 3. Проверка окончания периода (sim_end) каждую минуту — не ждём 30-мин цикла.
                #    active → settling (есть открытые позиции) или completed (все закрыты).
                for ts in sessions_list:
                    if ts.status != SessionStatus.active:
                        continue
                    if not ts.sim_end or now <= ts.sim_end:
                        continue
                    try:
                        open_count = await session.scalar(
                            select(func.count())
                            .select_from(Position)
                            .where(Position.session_id == ts.id, Position.status == PositionStatus.open)
                        )
                        if open_count == 0:
                            await _finalize_session(session, ts, status=SessionStatus.completed)
                            logger.info("session=%d завершена (фон): нет открытых позиций", ts.id)
                        else:
                            ts.status = SessionStatus.settling
                            await session.commit()
                            logger.info(
                                "session=%d → settling (фон): %d открытых позиций ждут резолюции",
                                ts.id, open_count,
                            )
                    except Exception:
                        logger.exception("Ошибка при проверке sim_end session=%d", ts.id)

                # 4. Авто-возобновление из паузы: если открытые позиции сыграли и equity
                #    восстановился выше порога просадки — бот сам выходит из паузы.
                if settings.risk_auto_resume:
                    for ts in sessions_list:
                        if ts.status != SessionStatus.paused:
                            continue
                        if ts.sim_end and now > ts.sim_end:
                            continue  # период истёк — не возобновляем
                        try:
                            equity = await _session_equity(session, ts)
                            threshold = ts.starting_balance * (1.0 - settings.risk_max_drawdown_pct)
                            if equity > threshold:
                                ts.status = SessionStatus.active
                                await session.commit()
                                logger.info(
                                    "session=%d auto-resume: equity=%.2f восстановился выше порога %.2f",
                                    ts.id, equity, threshold,
                                )
                                user = await session.get(User, ts.user_id)
                                if user:
                                    notify(user.telegram_id,
                                        f"▶️ Бот возобновлён — {ts.name or 'Бот'}\n\n"
                                        f"Открытые ставки отыгрались и вернули капитал выше порога.\n"
                                        f"Equity: ${equity:.2f} (порог: ${threshold:.2f})\n\n"
                                        f"Бот снова ищет новые лоты."
                                    )
                        except Exception:
                            logger.exception("Ошибка при авто-возобновлении session=%d", ts.id)

        except Exception:
            logger.exception("Ошибка в фоновой проверке стоп-лосса")
        finally:
            self._sl_running = False

    async def _run_impl(self) -> None:
        async with async_session_factory() as session:
            # Зафиксировать время старта цикла — используется при следующем запуске приложения
            # чтобы не запускать цикл сразу, а дождаться нужного интервала.
            state = await session.get(AppState, "last_cycle_at")
            now_iso = datetime.now(timezone.utc).isoformat()
            if state is None:
                session.add(AppState(key="last_cycle_at", value=now_iso))
            else:
                state.value = now_iso
            await session.commit()

            # 1. Обновить рынки из Gamma API (одно обращение на весь цикл).
            seen_condition_ids = await run_fetch(session)

            # 1b. Пометить closed рынки, ПРОПАВШИЕ из активного листинга.
            # Дата endDate сама по себе НЕ значит «закрыт»: Polymarket может держать
            # рынок торгуемым и после неё (турниры, отложенные матчи). Закрыт = рынок
            # больше не отдаётся в active=true&closed=false листинге.
            await _mark_expired_markets(session, seen_condition_ids)

            # 1c. Зафиксировать исходы зарезолвившихся проанализированных рынков
            #     (для метрик точности: Brier / hit rate / калибровка по моделям).
            try:
                await _record_resolutions(session)
            except Exception:
                logger.exception("Ошибка при записи резолюций")

            # 2. Прогнать цикл для каждой автоторговли в работе.
            #    settling — период истёк, но ждём резолюции открытых позиций:
            #    торговать не будет (см. _session_cycle), но settlement продолжается.
            active = await session.scalars(
                select(TradingSession).where(
                    TradingSession.status.in_([SessionStatus.active, SessionStatus.settling])
                )
            )
            for ts in active.all():
                try:
                    await self._session_cycle(session, ts)
                except DrawdownHalt as e:
                    ts.status = SessionStatus.paused
                    await session.commit()
                    logger.critical("DRAWDOWN HALT session=%d: %s", ts.id, e)
                    user = await session.get(User, ts.user_id)
                    if user:
                        notify(user.telegram_id,
                            f"⏸ Бот на паузе — просадка\n"
                            f"{ts.name or 'Бот'}\n\n"
                            f"Потери достигли -{settings.risk_max_drawdown_pct:.0%} от стартового капитала.\n"
                            f"Equity: ${e.equity:.2f} (старт: ${ts.starting_balance:.2f})\n\n"
                            f"Когда открытые позиции отыграются — бот возобновится автоматически."
                        )
                except Exception:
                    logger.exception("Ошибка в цикле session=%d", ts.id)

            # 3. Урегулировать позиции на закрытых рынках для приостановленных сессий.
            #    Paused-боты не запускают торговые циклы, но их позиции всё равно
            #    должны закрываться при завершении рынков.
            paused = await session.scalars(
                select(TradingSession).where(TradingSession.status == SessionStatus.paused)
            )
            for ts in paused.all():
                try:
                    u = await session.get(User, ts.user_id)
                    await _settle_resolved_positions(
                        session, ts.id, self.executor,
                        telegram_id=u.telegram_id if u else None,
                    )
                except Exception:
                    logger.exception("Ошибка при урегулировании paused session=%d", ts.id)

    async def _session_cycle(self, session: AsyncSession, ts: TradingSession) -> None:
        user = await session.get(User, ts.user_id)
        tid = user.telegram_id if user else None

        # Закрыть позиции на уже завершившихся рынках — до всего остального,
        # чтобы освободить капитал и не пытаться повторно анализировать эти рынки.
        await _settle_resolved_positions(session, ts.id, self.executor, telegram_id=tid)

        # Окончание периода автоторговли.
        # ВАЖНО: не закрываем позиции принудительно по стейл-цене — это рисовало бы
        # фейковый P&L на ещё не сыгравших рынках. Вместо этого перестаём открывать
        # новые/делать early-exit и ДЕРЖИМ позиции до их реальной резолюции
        # (их закроет _settle_resolved_positions, когда рынок действительно закроется).
        # Финализируем сессию только когда все позиции закрыты.
        if ts.sim_end and datetime.now(timezone.utc) > ts.sim_end:
            open_count = await session.scalar(
                select(func.count())
                .select_from(Position)
                .where(Position.session_id == ts.id, Position.status == PositionStatus.open)
            )
            if open_count == 0:
                realized = await session.scalar(
                    select(func.coalesce(func.sum(Position.pnl), 0.0))
                    .where(Position.session_id == ts.id, Position.status == PositionStatus.closed)
                )
                await _finalize_session(session, ts, status=SessionStatus.completed)
                logger.info("session=%d завершена: все позиции урегулированы", ts.id)
                realized = float(realized or 0.0)
                pct = realized / ts.starting_balance * 100 if ts.starting_balance else 0.0
                pnl_str = f"+${realized:.2f}" if realized >= 0 else f"-${abs(realized):.2f}"
                notify(tid,
                    f"🏁 Бот завершил работу — {ts.name or 'Бот'}\n\n"
                    f"Итоговый P&L: {pnl_str} ({pct:+.1f}%)\n"
                    f"Капитал возвращён в кошелёк."
                )
            else:
                # Перевести в settling (один раз) — освобождает слот активных ботов,
                # в UI показывается как «Ожидание результатов».
                if ts.status != SessionStatus.settling:
                    ts.status = SessionStatus.settling
                    await session.commit()
                logger.info(
                    "session=%d период истёк, ждём резолюции %d открытых позиций",
                    ts.id, open_count,
                )
            return

        # Досрочный выход: ревизия открытых позиций (стоп-лосс + AI-переоценка).
        # До открытия новых — чтобы освободить капитал под свежие сигналы.
        if settings.early_exit_enabled:
            await self._manage_open_positions(session, ts)

        risk = RiskManager(starting_bankroll=ts.starting_balance)
        # Просадка по ПОЛНОЙ стоимости бота (свободный баланс + открытые позиции),
        # а не по одному свободному балансу — иначе размещение капитала в позиции
        # ложно выглядит как просадка.
        equity = await _session_equity(session, ts)
        risk.check_drawdown(equity)

        now = datetime.now(timezone.utc)

        # Кандидаты: открытые рынки, закрывающиеся ДО окончания периода бота,
        # без уже открытой позиции на том же рынке и без ранее проанализированных рынков.
        open_market_ids = select(Position.market_id).where(
            Position.session_id == ts.id,
            Position.status == PositionStatus.open,
        )
        # Рынки, которые уже прошли решение (decision-phase) в предыдущих циклах этого бота.
        # Исключаем их чтобы каждый цикл работал со свежими рынками.
        analyzed_market_ids = select(Analysis.market_id).where(
            Analysis.session_id == ts.id,
        )
        stmt = (
            select(Market, MarketToken)
            .join(MarketToken, MarketToken.market_id == Market.id)
            .where(Market.status == MarketStatus.open)
            .where(Market.id.not_in(open_market_ids))
            .where(Market.id.not_in(analyzed_market_ids))
            # Рынок не должен закрываться слишком скоро: иначе стоп-лосс/переоценка
            # не успеют сработать (резолвится между 30-мин циклами → гэп в ноль).
            .where(Market.close_time > now + timedelta(hours=settings.risk_min_hours_to_close))
        )
        if ts.sim_end:
            # только рынки, результат которых придёт ДО окончания работы бота
            stmt = stmt.where(Market.close_time <= ts.sim_end)
        if ts.category:
            stmt = stmt.where(Market.category == ts.category)
        rows = await session.execute(stmt)
        candidates = list(rows.all())

        logger.info(
            "session=%d candidates=%d (period до %s, category=%s)",
            ts.id, len(candidates),
            ts.sim_end.isoformat() if ts.sim_end else "∞",
            ts.category or "все",
        )

        # Фильтрация дешёвой моделью. Берём топ по объёму, не больше MAX_FILTER_ATTEMPTS
        # обращений к LLM за цикл — защита от выжигания rate limit Groq free tier.
        candidates.sort(key=lambda row: (row[0].volume or 0), reverse=True)
        finalists: list[tuple[Market, MarketToken, float]] = []
        seen_market_ids: set[int] = set()   # один рынок — один финалист за цикл
        filter_calls = 0
        for market, token in candidates:
            if token.last_price is None:
                continue
            # Один токен на рынок: если Yes уже прошёл фильтр, No пропускаем (и наоборот).
            # Это предотвращает хедж YES+NO одного рынка внутри одного цикла.
            if market.id in seen_market_ids:
                continue
            # Анализируем КОНКРЕТНЫЙ исход (этот токен) в его собственной системе
            # координат: market_prob = цена самого токена = P(этот исход). AI оценивает
            # P(этот исход), edge считается относительно него. Это убирает рассинхрон
            # «назвали исход X, дали цену не-X», который ломал не-Yes/No рынки.
            prob = token.last_price
            # Рынок почти решён (≥95% или ≤5%) — upside нет, пропускаем без LLM-вызова.
            # Также пропускаем токены дешевле минимального порога (по умолчанию <30%):
            # крайне низкая вероятность = малая ликвидность и огромный спред.
            if not (settings.risk_min_market_prob <= prob <= settings.risk_max_market_prob):
                continue
            if filter_calls >= MAX_FILTER_ATTEMPTS:
                break
            await asyncio.sleep(settings.llm_filter_delay)
            filt = await self.analyzer.filter_market(_payload(market, token, prob))
            filter_calls += 1
            seen_market_ids.add(market.id)
            if self.analyzer.has_edge(filt, prob):
                finalists.append((market, token, prob))
            else:
                # Рынок проверен фильтром и не прошёл — фиксируем NEUTRAL-след,
                # чтобы analyzed_market_ids исключил его в следующих циклах и фильтр
                # доходил до НОВЫХ лотов, а не пережёвывал те же высокообъёмные no-edge.
                # (Реанализ позиций/статусов идёт отдельно и не затрагивается.)
                session.add(Analysis(
                    session_id=ts.id,
                    market_id=market.id,
                    token_id=token.token_id,
                    model=filt.model,
                    market_prob=prob,
                    my_prob=filt.prob,
                    edge=self.analyzer.edge(filt, prob),
                    verdict=Verdict.NEUTRAL,
                    reasoning="[filter] нет edge",
                ))
            if len(finalists) >= MAX_FINALISTS:
                break

        logger.info("session=%d filter: %d вызовов → %d финалистов", ts.id, filter_calls, len(finalists))

        # Текущая экспозиция автоторговли.
        exposure = await _session_exposure(session, ts.id)

        # Экспозиция по СОБЫТИЯМ (для лимита на коррелированные рынки одного матча/
        # города-дня). Ключ — заголовок события (первая строка описания рынка).
        event_exposure, event_count = await _session_event_exposure(session, ts.id)

        # Решение дорогой моделью → риск → сделка.
        for market, token, prob in finalists:
            payload = _payload(market, token, prob)
            await asyncio.sleep(settings.llm_decision_delay)
            result = await self.analyzer.decide(payload)
            edge = self.analyzer.edge(result, prob)   # оба = P(анализируемый исход)

            session.add(Analysis(
                session_id=ts.id,
                market_id=market.id,
                token_id=token.token_id,
                model=result.model,
                market_prob=prob,   # цена анализируемого исхода (для сравнения в UI)
                my_prob=result.prob,
                edge=edge,
                verdict=Verdict(result.verdict),
                reasoning=result.reasoning,
            ))
            await session.flush()

            if result.verdict == "NEUTRAL":
                continue

            # Для Kelly-сайзинга: вероятность выигрыша ПОКУПАЕМОГО токена и цена
            # контракта (зависят от направления). В режиме "fixed" не используются.
            #   BUY_YES → покупаем анализируемый токен: win=my_prob, price=его цена.
            #   BUY_NO  → покупаем противоположный: win=1−my_prob, price=1−цена.
            if result.verdict == "BUY_YES":
                win_prob, price = result.prob, prob
            else:
                win_prob, price = 1.0 - result.prob, 1.0 - prob

            decision = risk.approve(
                bankroll=ts.balance,
                current_exposure=exposure,
                edge=edge,
                market_volume=market.volume,
                win_prob=win_prob,
                price=price,
            )
            if not decision.allowed:
                logger.info("session=%d risk deny '%s': %s", ts.id, market.question[:40], decision.reason)
                continue

            # Лимит на ОДНО событие: не больше N ставок и не больше X% капитала.
            # Рынки одного матча/города-дня коррелированы — это концентрация, не
            # диверсификация. Без заголовка (нет описания) рынок не группируем.
            ek = _event_key(market.description)
            if ek:
                if event_count.get(ek, 0) >= settings.risk_max_positions_per_event:
                    logger.info(
                        "session=%d skip '%s': лимит ставок на событие (%d) — '%s'",
                        ts.id, market.question[:40], settings.risk_max_positions_per_event, ek[:34],
                    )
                    continue
                event_cap = settings.risk_max_event_exposure_pct * ts.starting_balance
                if event_exposure.get(ek, 0.0) + decision.size_usdc > event_cap:
                    logger.info(
                        "session=%d skip '%s': лимит экспозиции на событие (%.0f%%) — '%s'",
                        ts.id, market.question[:40], settings.risk_max_event_exposure_pct * 100, ek[:34],
                    )
                    continue

            # Выбираем токен, которым будем ЛОНГ (на Polymarket нельзя шортить):
            #   BUY_YES → сам анализируемый токен (ставим НА этот исход),
            #   BUY_NO  → противоположный токен бинарного рынка (ставим ПРОТИВ).
            # Так убыток ограничен ставкой, а P&L считается единой лонговой формулой.
            buy_token = await _pick_buy_token(session, market.id, token, result.verdict)
            if buy_token is None:
                logger.info(
                    "session=%d skip '%s': не бинарный рынок, BUY_NO небезопасен",
                    ts.id, market.question[:40],
                )
                continue

            side = "YES" if result.verdict == "BUY_YES" else "NO"

            # Вход по РЕАЛЬНОЙ цене покупки (ask) из живого стакана: на Polymarket
            # покупка исполняется по лучшему ask. Спред (ask выше mid) — реальная
            # издержка, которую в бумажном режиме тоже надо платить, иначе доходность
            # завышается. Нет ask (продавцов нет / стакан пуст) — войти нельзя,
            # пропускаем (симметрично выходу по bid). Сигнал/edge остаётся на
            # справедливой цене (last_price), ask влияет только на цену исполнения.
            quote = await fetch_token_quote(buy_token.token_id)
            ask = quote[1] if quote else None
            if ask is None:
                logger.info(
                    "session=%d skip '%s': нет ask в стакане — войти нельзя",
                    ts.id, market.question[:40],
                )
                continue
            entry_price = ask
            pos = await self.executor.open_position(
                session,
                trading_session=ts,
                market_id=market.id,
                token_id=buy_token.token_id,
                side=side,
                size_usdc=decision.size_usdc,
                entry_price=entry_price,
                reasoning=result.reasoning,
            )
            if pos:
                exposure += decision.size_usdc
                if ek:
                    event_exposure[ek] = event_exposure.get(ek, 0.0) + decision.size_usdc
                    event_count[ek] = event_count.get(ek, 0) + 1
                outcome_str = (token.outcome
                               if token.outcome and token.outcome.lower() not in ("yes", "no")
                               else side)
                direction = "НА" if result.verdict == "BUY_YES" else "ПРОТИВ"
                notify(tid,
                    f"🟢 Новая ставка\n"
                    f"{market.question[:60]}\n\n"
                    f"ставка {direction} «{outcome_str}» · ${decision.size_usdc:.2f}\n"
                    f"Рынок: {prob*100:.0f}% · Модель: {result.prob*100:.0f}% · Edge: {edge:+.1%}"
                )

        # Обновить current_price открытых позиций.
        await _update_open_prices(session, ts.id)

    async def _manage_open_positions(self, session: AsyncSession, ts: TradingSession) -> None:
        """Ревизия открытых позиций на ещё торгуемых рынках (гибридный выход).

        Позиция всегда ЛОНГ удерживаемого токена (YES и NO держат разные токены).

        Для каждой позиции на открытом рынке:
          1. Тянем живой стакан удерживаемого токена.
          2. Нереализованный P&L по реальной цене ВЫХОДА = продаём по bid.
          3. (Стоп-лосс вынесен в частую фоновую задачу run_stop_loss_check.)
          4. AI-переоценка тезиса: модель видит удерживаемый исход
             переоценённым (edge против нас на ≥ порога) → выходим.

        Выход возможен ТОЛЬКО пока стакан жив (есть bid). Если рынок закрыт
        или покупателей нет — позицию не трогаем, её добьёт settlement."""
        now = datetime.now(timezone.utc)
        open_positions = await session.scalars(
            select(Position)
            .join(Market, Market.id == Position.market_id)
            .where(
                Position.session_id == ts.id,
                Position.status == PositionStatus.open,
                Market.status == MarketStatus.open,
                Market.close_time > now,   # рынок ещё торгуется
            )
        )
        for pos in open_positions.all():
            token = await session.get(MarketToken, pos.token_id)
            if token is None:
                continue

            quote = await fetch_token_quote(pos.token_id)
            if quote is None:
                continue  # стакан пуст — выйти нельзя, ждём резолюции
            bid, ask = quote

            # Лонг: выходим продажей по bid. Нет покупателей — выйти нельзя.
            if bid is None:
                continue
            exit_price = bid

            shares = pos.size_usdc / pos.entry_price if pos.entry_price else 0.0
            unrealized = (exit_price - pos.entry_price) * shares

            # Отметить позицию по рынку (mid, либо доступная сторона) для UI.
            mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else bid
            pos.current_price = mid
            await session.commit()

            # 1. Стоп-лосс вынесен в частую фоновую задачу run_stop_loss_check
            #    (раз в ~60с, без LLM) — здесь только AI-переоценка тезиса.

            # 2. AI-переоценка тезиса — анализируем УДЕРЖИВАЕМЫЙ токен в его собственной
            #    системе координат (market_prob = его текущая цена = P(его исход)).
            #    Делаем это ТОЛЬКО если цена реально сдвинулась с прошлой проверки этого
            #    токена: на тех же данных модель даст тот же ответ — экономим LLM-вызов
            #    и не ловим шумовой флип. Сравниваем с последней записью по ЭТОМУ токену
            #    (а не по рынку: при BUY_NO держим противоположный токен), иначе — с entry.
            price_now = mid   # текущая цена удерживаемого токена
            last_prob = await session.scalar(
                select(Analysis.market_prob)
                .where(
                    Analysis.session_id == ts.id,
                    Analysis.market_id == pos.market_id,
                    Analysis.token_id == pos.token_id,
                )
                .order_by(Analysis.id.desc())
                .limit(1)
            )
            ref_prob = last_prob if last_prob is not None else pos.entry_price
            if abs(price_now - ref_prob) < settings.early_exit_reeval_price_move:
                continue  # цена не сдвинулась — держим без переоценки

            market = await session.get(Market, pos.market_id)
            if market is None:
                continue
            payload = _payload(market, token, price_now)
            await asyncio.sleep(settings.llm_decision_delay)
            try:
                result = await self.analyzer.decide(payload)
            except Exception:
                logger.exception("session=%d re-eval pos=%d упал — оставляем позицию", ts.id, pos.id)
                continue
            edge_now = self.analyzer.edge(result, price_now)   # P(исход)_model − цена

            # Выход СИММЕТРИЧЕН входу: держим лонг этого токена, выходим только если
            # модель видит его ПЕРЕОЦЕНЁННЫМ на ≥ порога (edge ушёл против нас). НЕ
            # выходим на слабом edge — это шум стохастичной модели (temp 0.2) и даёт
            # churn (закрытие только что открытых позиций, потеря спреда).
            thr = settings.early_exit_ai_edge_threshold
            thesis_broke = edge_now <= -thr

            # Зафиксировать переоценку (для аудита/UI кривой точности).
            session.add(Analysis(
                session_id=ts.id,
                market_id=pos.market_id,
                token_id=pos.token_id,
                model=result.model,
                market_prob=price_now,
                my_prob=result.prob,
                edge=edge_now,
                verdict=Verdict(result.verdict),
                reasoning=f"[re-eval] {result.reasoning}",
            ))
            await session.commit()

            if thesis_broke:
                await self.executor.close_position(session, pos, exit_price)
                logger.info(
                    "session=%d AI-EXIT pos=%d outcome=%s verdict=%s edge=%+.3f exit@%.3f pnl=%.2f",
                    ts.id, pos.id, token.outcome, result.verdict, edge_now, exit_price, unrealized,
                )


async def settle_session(
    session: AsyncSession,
    executor: Executor,
    ts: TradingSession,
    *,
    status: SessionStatus,
) -> None:
    """РУЧНАЯ остановка (/stop): ликвидировать все позиции СЕЙЧАС и вернуть баланс.

    Пользователь хочет выйти немедленно. Позиция всегда ЛОНГ удерживаемого токена:
      - рынок реально зарезолвился → бинарный исход (1.0/0.0);
      - рынок ещё торгуется → продаём по реальному bid из живого стакана;
      - стакана нет / нет bid → выход по последней известной цене (fallback).

    (Истечение периода — НЕ здесь: там позиции держатся до резолюции, см. _session_cycle.)"""
    open_positions = await session.scalars(
        select(Position)
        .join(Market, Market.id == Position.market_id)
        .where(Position.session_id == ts.id, Position.status == PositionStatus.open)
    )
    for pos in open_positions.all():
        market = await session.get(Market, pos.market_id)
        token = await session.get(MarketToken, pos.token_id)

        market_resolved = market and market.status == MarketStatus.closed
        if market_resolved:
            # Рынок закрыт — запросить реальную цену резолюции.
            real_price = await fetch_token_resolution(market.condition_id, pos.token_id) if market else None
            if real_price is not None and token:
                token.last_price = real_price
            use_price = real_price if real_price is not None else (token.last_price if token and token.last_price is not None else pos.entry_price)

            if _RESOLUTION_THRESHOLD < use_price < (1.0 - _RESOLUTION_THRESHOLD):
                await executor.close_position(session, pos, use_price)
            else:
                exit_price = 1.0 if use_price >= (1.0 - _RESOLUTION_THRESHOLD) else 0.0
                await executor.close_position(session, pos, exit_price)
        else:
            # Рынок ещё торгуется — продаём по реальному bid (как на Polymarket).
            quote = await fetch_token_quote(pos.token_id)
            bid = quote[0] if quote else None
            exit_price = bid if bid is not None else (token.last_price if token and token.last_price is not None else pos.entry_price)
            await executor.close_position(session, pos, exit_price)

    await _finalize_session(session, ts, status=status)


async def _finalize_session(
    session: AsyncSession, ts: TradingSession, *, status: SessionStatus
) -> None:
    """Финализировать сессию: вернуть свободный баланс в кошелёк юзера, проставить статус.
    Позиции к этому моменту уже должны быть закрыты (баланс отражает только свободные средства)."""
    user = await session.get(User, ts.user_id)
    if user:
        user.virtual_balance += ts.balance
    ts.balance = 0.0
    ts.status = status
    await session.commit()


async def _session_exposure(session: AsyncSession, session_id: int) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(Position.size_usdc), 0.0))
        .where(Position.session_id == session_id, Position.status == PositionStatus.open)
    )
    return float(total or 0.0)


def _event_key(description: str | None) -> str | None:
    """Ключ события для группировки коррелированных рынков: первая строка описания
    (заголовок события Gamma — общий у всех рынков одного матча/города-дня).
    None, если описания нет → рынок не группируем (действует обычный лимит на рынок)."""
    if not description:
        return None
    line = description.split("\n", 1)[0].strip()
    return line or None


async def _session_event_exposure(
    session: AsyncSession, session_id: int
) -> tuple[dict[str, float], dict[str, int]]:
    """Сумма и число открытых позиций по СОБЫТИЯМ сессии (ключ — заголовок события).
    Для лимита на коррелированные рынки одного матча/города-дня."""
    rows = await session.execute(
        select(Position.size_usdc, Market.description)
        .join(Market, Market.id == Position.market_id)
        .where(Position.session_id == session_id, Position.status == PositionStatus.open)
    )
    exposure: dict[str, float] = {}
    count: dict[str, int] = {}
    for size, description in rows.all():
        key = _event_key(description)
        if not key:
            continue
        exposure[key] = exposure.get(key, 0.0) + float(size)
        count[key] = count.get(key, 0) + 1
    return exposure, count


async def _session_equity(session: AsyncSession, ts: TradingSession) -> float:
    """Полная стоимость бота = свободный баланс + mark-to-market открытых позиций.

    Просадку считаем по equity, а НЕ по balance: balance падает просто от размещения
    капитала в позиции (executor: open → balance -= size), даже без реального убытка.
    Иначе бот ложно встаёт на DRAWDOWN HALT, едва задействовав экспозицию."""
    open_positions = await session.scalars(
        select(Position).where(
            Position.session_id == ts.id,
            Position.status == PositionStatus.open,
        )
    )
    positions_value = 0.0
    for pos in open_positions.all():
        if not pos.entry_price:
            continue
        price = pos.current_price if pos.current_price is not None else pos.entry_price
        shares = pos.size_usdc / pos.entry_price
        positions_value += shares * price
    return ts.balance + positions_value


async def _update_open_prices(session: AsyncSession, session_id: int) -> None:
    """Подтянуть текущие цены токенов в открытые позиции (для P&L в реальном времени)."""
    open_positions = await session.scalars(
        select(Position)
        .where(Position.session_id == session_id, Position.status == PositionStatus.open)
    )
    for pos in open_positions.all():
        token = await session.get(MarketToken, pos.token_id)
        if token and token.last_price is not None:
            pos.current_price = token.last_price
    await session.commit()


_RESOLUTION_BATCH = 25   # макс. рынков на резолюцию за цикл (бережём Gamma API)


async def _record_resolutions(session: AsyncSession) -> None:
    """Зафиксировать исходы зарезолвившихся проанализированных рынков в Resolution.

    Берём рынки, по которым был анализ, ещё нет записи Resolution и close_time прошёл.
    Спрашиваем у Gamma победивший исход; если рынок реально зарезолвился — пишем.
    Это источник истины для метрик точности (Brier / hit rate по моделям).
    Полностью аддитивно: торговую логику не трогает."""
    now = datetime.now(timezone.utc)
    analyzed = select(Analysis.market_id).distinct()
    resolved = select(Resolution.market_id)
    markets = (await session.scalars(
        select(Market)
        .where(
            Market.id.in_(analyzed),
            Market.id.not_in(resolved),
            Market.close_time <= now,
        )
        .limit(_RESOLUTION_BATCH)
    )).all()

    recorded = 0
    for market in markets:
        winner = await fetch_market_winner(market.condition_id)
        if winner is None:
            continue  # ещё не зарезолвился — попробуем в следующем цикле
        session.add(Resolution(market_id=market.id, winning_outcome=winner))
        recorded += 1
    if recorded:
        await session.commit()
        logger.info("Зафиксировано %d исходов рынков (метрики точности)", recorded)


async def _mark_expired_markets(session: AsyncSession, seen_condition_ids: set[str]) -> None:
    """Пометить closed рынки, которые ПРОПАЛИ из активного листинга Polymarket.

    Признак реального закрытия — рынок больше не отдаётся в active=true&closed=false
    (значит зарезолвился/архивирован), А НЕ просто истёкшая дата. Рынок, всё ещё
    присутствующий в листинге (seen_condition_ids), остаётся open даже после endDate.

    Доп. защита: если рынок ошибочно помечен closed, но снова появился в листинге,
    save_markets вернёт его в open (самовосстановление)."""
    now = datetime.now(timezone.utc)
    stmt = (
        sa_update(Market)
        .where(
            Market.status == MarketStatus.open,
            Market.close_time <= now,
        )
        .values(status=MarketStatus.closed)
    )
    if seen_condition_ids:
        stmt = stmt.where(Market.condition_id.not_in(seen_condition_ids))
    result = await session.execute(stmt)
    if result.rowcount:
        logger.info("Закрыто %d рынков (пропали из активного листинга)", result.rowcount)
    await session.commit()


_RESOLUTION_THRESHOLD = 0.02  # цена считается финальной если ≤ 0.02 или ≥ 0.98


async def _settle_resolved_positions(
    session: AsyncSession, session_id: int, executor: Executor,
    *, telegram_id: int | None = None,
) -> None:
    """Закрыть открытые позиции на рынках с истёкшим close_time.

    Для каждой позиции запрашиваем актуальную цену токена с Gamma API.
    Если рынок реально зарезолвился (цена ≤ 0.02 или ≥ 0.98) — закрываем.
    Если цена стейл (в середине) — пропускаем до следующего цикла."""
    now = datetime.now(timezone.utc)
    open_on_closed = await session.scalars(
        select(Position)
        .join(Market, Market.id == Position.market_id)
        .where(
            Position.session_id == session_id,
            Position.status == PositionStatus.open,
            # Также проверяем рынки с истёкшим close_time, даже если они ещё числятся
            # open в нашей БД: Polymarket держит рынки в активном листинге во время
            # урегулирования. fetch_token_resolution (closed=true) — реальный арбитр:
            # вернёт None пока Gamma не опубликовал финальный исход → ждём.
            or_(
                Market.status == MarketStatus.closed,
                Market.close_time <= now,
            ),
        )
    )
    for pos in open_on_closed.all():
        market = await session.get(Market, pos.market_id)
        if not market:
            continue

        # Получить реальную цену с Gamma API, не стейл из БД.
        real_price = await fetch_token_resolution(market.condition_id, pos.token_id)

        if real_price is None:
            logger.info("session=%d pos=%d: Gamma не вернул цену, пропускаем", session_id, pos.id)
            continue

        # Обновить last_price в БД
        token = await session.get(MarketToken, pos.token_id)
        if token:
            token.last_price = real_price

        # Рынок ещё не зарезолвился — цена в середине значит стейл
        if _RESOLUTION_THRESHOLD < real_price < (1.0 - _RESOLUTION_THRESHOLD):
            logger.info(
                "session=%d pos=%d: цена=%.4f — рынок ещё не зарезолвился, ждём",
                session_id, pos.id, real_price,
            )
            await session.commit()
            continue

        # Позиция всегда ЛОНГ удерживаемого токена: выигрыш = этот токен → 1.
        # Закрываем по бинарному исходу (1.0 / 0.0), единая лонговая формула в
        # close_position сама даст pnl = (exit - entry) * shares (убыток ≤ ставки).
        token_won = real_price >= (1.0 - _RESOLUTION_THRESHOLD)
        exit_price = 1.0 if token_won else 0.0
        await executor.close_position(session, pos, exit_price)

        logger.info(
            "session=%d auto-settle pos=%d side=%s token_price=%.4f → %s",
            session_id, pos.id, pos.side, real_price, "WIN" if token_won else "LOSS",
        )
        pnl = pos.pnl or 0.0
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        emoji = "✅" if token_won else "❌"
        result_word = "Выиграл!" if token_won else "Проиграл"
        notify(telegram_id,
            f"{emoji} {result_word}\n"
            f"{market.question[:60]}\n\n"
            f"P&L: {pnl_str}"
        )


async def _pick_buy_token(
    session: AsyncSession, market_id: int, analyzed: MarketToken, verdict: str
) -> MarketToken | None:
    """Выбрать токен, который реально покупаем (всегда лонг).

    Анализируем КОНКРЕТНЫЙ исход (`analyzed`), вердикт — относительно него:
      BUY_YES → ставим НА этот исход → покупаем сам `analyzed`.
      BUY_NO  → ставим ПРОТИВ → покупаем противоположный токен (только бинарный рынок).

    Возвращает None для BUY_NO на НЕ бинарном рынке (≠2 токена): «противоположный»
    там неоднозначен (вероятность размазана по нескольким исходам), шорт небезопасен."""
    if verdict == "BUY_YES":
        return analyzed
    # BUY_NO: нужен противоположный токен бинарного рынка.
    tokens = (
        await session.scalars(select(MarketToken).where(MarketToken.market_id == market_id))
    ).all()
    if len(tokens) != 2:
        return None
    return next(t for t in tokens if t.token_id != analyzed.token_id)


def _payload(market: Market, token: MarketToken, market_prob: float) -> dict:
    return {
        "question": market.question,
        "description": market.description,   # заголовок события + описание (соперник, турнир и т.д.)
        "outcome": token.outcome,
        "market_prob": market_prob,   # цена анализируемого исхода = P(этот исход)
        "close_time": market.close_time.isoformat() if market.close_time else None,
        "volume": market.volume,
        "category": market.category,
    }


trading_cycle = TradingCycle()
