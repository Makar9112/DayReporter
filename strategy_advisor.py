"""
Рекомендации по торговой стратегии на основе журнала заявок.
Помогает держать ритм подачи в безопасной зоне относительно критериев №3–№4 и лимитов.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import pandas as pd

from trade_analytics import instruments_order_counts
from criteria import (
    STATUS_OK,
    STATUS_VIOLATED,
    CriterionResult,
    _format_sec,
    _max_burst_in_window,
    burst_diagnostics,
)

# Пороги критериев (для запаса)
CRIT4_BURST = 3
CRIT4_WINDOW_MS = 100
CRIT3_BURST = 7
CRIT3_WINDOW_S = 1.0

# Рекомендуемые безопасные значения (запас ~20–30%)
SAFE_MAX_IN_100MS = 2
SAFE_MIN_GAP_MS = 150
SAFE_MAX_IN_1S = 5
SAFE_MIN_GAP_1S = 1.2

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

PRIORITY_ORDER = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 1, PRIORITY_LOW: 2}

PRIORITY_LABELS = {
    PRIORITY_HIGH: "Высокий",
    PRIORITY_MEDIUM: "Средний",
    PRIORITY_LOW: "Низкий",
}


@dataclass
class StrategyTip:
    """Одна рекомендация по улучшению стратегии."""

    priority: str
    category: str
    title: str
    message: str
    evidence: str
    action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyReport:
    """Итог советника: балл, советы, план на следующую сессию."""

    discipline_score: int
    rhythm_grade: str
    tips: List[StrategyTip]
    session_plan: List[str]
    rhythm_stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discipline_score": self.discipline_score,
            "rhythm_grade": self.rhythm_grade,
            "tips": [t.to_dict() for t in self.tips],
            "session_plan": self.session_plan,
            "rhythm_stats": self.rhythm_stats,
        }


def _grade_from_score(score: int) -> str:
    if score >= 85:
        return "Отлично"
    if score >= 70:
        return "Хорошо"
    if score >= 50:
        return "Удовлетворительно"
    return "Требует внимания"


def _compute_buy_gaps(buys: pd.DataFrame) -> pd.Series:
    """Интервалы между последовательными покупками (секунды)."""
    if buys.empty or "Время_сек" not in buys.columns:
        return pd.Series(dtype=float)
    times = buys["Время_сек"].dropna().sort_values()
    if len(times) < 2:
        return pd.Series(dtype=float)
    return times.diff().dropna()


def _rhythm_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """Сводная статистика ритма покупок."""
    diag = burst_diagnostics(df)
    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    gaps = _compute_buy_gaps(buys)

    stats: Dict[str, Any] = {
        **diag,
        "buy_count": int(len(buys)),
        "gap_count": int(len(gaps)),
    }

    if len(gaps):
        stats["gap_median_ms"] = round(float(gaps.median()) * 1000, 1)
        stats["gap_p10_ms"] = round(float(gaps.quantile(0.10)) * 1000, 1)
        stats["gap_min_ms"] = round(float(gaps.min()) * 1000, 1)
        stats["gaps_under_100ms"] = int((gaps < 0.1).sum())
        stats["gaps_under_1s"] = int((gaps < 1.0).sum())
    else:
        stats.update(
            {
                "gap_median_ms": None,
                "gap_p10_ms": None,
                "gap_min_ms": None,
                "gaps_under_100ms": 0,
                "gaps_under_1s": 0,
            }
        )

    return stats


def _discipline_score(
    stats: Dict[str, Any],
    instrument_limit: int,
    day_limit_c6: int,
    results: List[CriterionResult],
    df: pd.DataFrame,
) -> int:
    """Индекс дисциплины 0–100 (чем выше — тем дальше от порогов нарушений)."""
    score = 100

    max_100 = stats.get("max_100ms", 0)
    if max_100 >= CRIT4_BURST:
        score -= 35
    elif max_100 >= SAFE_MAX_IN_100MS:
        score -= 18

    max_1s = stats.get("max_1s", 0)
    if max_1s >= CRIT3_BURST:
        score -= 30
    elif max_1s >= SAFE_MAX_IN_1S + 1:
        score -= 15
    elif max_1s >= SAFE_MAX_IN_1S:
        score -= 8

    for r in results:
        if r.status == STATUS_VIOLATED:
            score -= 8
        elif r.status not in (STATUS_OK,):
            score -= 3

    counts = instruments_order_counts(df)
    if not counts.empty:
        max_cnt = int(counts["Количество"].max())
        if max_cnt > instrument_limit:
            score -= 25
        elif max_cnt > instrument_limit * 0.9:
            score -= 12
        elif max_cnt > instrument_limit * 0.75:
            score -= 5

    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    if not buys.empty and "Код инструмента" in buys.columns:
        per_inst = buys.groupby("Код инструмента").size()
        if (per_inst > day_limit_c6).any():
            score -= 20
        elif (per_inst > day_limit_c6 * 0.85).any():
            score -= 10

    gap_min = stats.get("gap_min_ms")
    if gap_min is not None and gap_min < SAFE_MIN_GAP_MS:
        score -= 10

    return max(0, min(100, score))


def _tip_rhythm_100ms(stats: Dict[str, Any]) -> Optional[StrategyTip]:
    max_100 = stats.get("max_100ms", 0)
    start = stats.get("start_100ms")
    if max_100 < SAFE_MAX_IN_100MS:
        return None

    if max_100 >= CRIT4_BURST:
        priority = PRIORITY_HIGH
        title = "Риск критерия №4 (100 мс)"
        message = (
            f"Зафиксировано {max_100} покупок за ≤{CRIT4_WINDOW_MS} мс "
            f"(порог нарушения: ≥{CRIT4_BURST})."
        )
        action = (
            f"Держите не более {SAFE_MAX_IN_100MS} заявок за 100 мс по сессии и инструменту. "
            f"Минимальная пауза между покупками: ≥{SAFE_MIN_GAP_MS} мс."
        )
    else:
        priority = PRIORITY_MEDIUM
        title = "Ритм близок к критерию №4"
        message = (
            f"Максимум {max_100} покупок за 100 мс — на грани порога {CRIT4_BURST}."
        )
        action = (
            f"Увеличьте паузу до ≥{SAFE_MIN_GAP_MS} мс, чтобы оставался запас."
        )

    evidence = f"Серия с {_format_sec(start)}." if start is not None else "По данным сессии."
    return StrategyTip(
        priority=priority,
        category="rhythm",
        title=title,
        message=message,
        evidence=evidence,
        action=action,
    )


def _tip_rhythm_1s(stats: Dict[str, Any]) -> Optional[StrategyTip]:
    max_1s = stats.get("max_1s", 0)
    start = stats.get("start_1s")
    if max_1s < SAFE_MAX_IN_1S:
        return None

    if max_1s >= CRIT3_BURST:
        priority = PRIORITY_HIGH
        title = "Риск критерия №3 (1 с)"
        message = (
            f"Зафиксировано {max_1s} покупок за ≤1 с (порог нарушения: ≥{CRIT3_BURST})."
        )
        action = (
            f"Не более {SAFE_MAX_IN_1S} покупок за 1 с. "
            f"Целевой интервал между заявками: ≥{SAFE_MIN_GAP_1S} с."
        )
    else:
        priority = PRIORITY_MEDIUM
        title = "Высокая частота за 1 с"
        message = f"Максимум {max_1s} покупок за 1 с (порог критерия №3: {CRIT3_BURST})."
        action = f"Снизьте темп: пауза ≥{SAFE_MIN_GAP_1S} с между подачами."

    evidence = f"Серия с {_format_sec(start)}." if start is not None else "По данным сессии."
    return StrategyTip(
        priority=priority,
        category="rhythm",
        title=title,
        message=message,
        evidence=evidence,
        action=action,
    )


def _tip_gap_distribution(stats: Dict[str, Any]) -> Optional[StrategyTip]:
    gap_min = stats.get("gap_min_ms")
    gaps_u100 = stats.get("gaps_under_100ms", 0)
    if gap_min is None:
        return None

    if gap_min >= SAFE_MIN_GAP_MS and gaps_u100 <= 2:
        return None

    if gap_min < 50 or gaps_u100 >= 10:
        priority = PRIORITY_HIGH
    elif gap_min < SAFE_MIN_GAP_MS or gaps_u100 >= 5:
        priority = PRIORITY_MEDIUM
    else:
        priority = PRIORITY_LOW

    median = stats.get("gap_median_ms")
    return StrategyTip(
        priority=priority,
        category="rhythm",
        title="Неравномерный ритм подачи",
        message=(
            f"Минимальный интервал между покупками: {gap_min} мс; "
            f"пауз короче 100 мс: {gaps_u100}."
            + (f" Медиана интервала: {median} мс." if median is not None else "")
        ),
        evidence="Интервалы между последовательными покупками в сессии.",
        action=(
            f"Задайте себе нижнюю границу: не чаще одной покупки каждые {SAFE_MIN_GAP_MS} мс "
            f"(лучше 200–300 мс при активной торговле)."
        ),
    )


def _tips_per_instrument(df: pd.DataFrame) -> List[StrategyTip]:
    """Советы по инструментам с частыми сериями."""
    tips: List[StrategyTip] = []
    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    if buys.empty or "Код инструмента" not in buys.columns:
        return tips

    for code, group in buys.groupby("Код инструмента"):
        times = group["Время_сек"].dropna()
        mc100, st100 = _max_burst_in_window(times, 0.1)
        mc1, st1 = _max_burst_in_window(times, 1.0)
        cnt = len(group)

        if mc100 >= CRIT4_BURST:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_HIGH,
                    category="instrument",
                    title=f"Перегрев по {code} (100 мс)",
                    message=f"{mc100} покупок за ≤100 мс по одному инструменту.",
                    evidence=f"Серия с {_format_sec(st100)}, всего заявок: {cnt}.",
                    action=(
                        f"По {code} выдерживайте паузу ≥{SAFE_MIN_GAP_MS} мс; "
                        "чередуйте инструменты, если нужно ускориться."
                    ),
                )
            )
        elif mc100 >= SAFE_MAX_IN_100MS:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_MEDIUM,
                    category="instrument",
                    title=f"Частые подачи по {code}",
                    message=f"{mc100} покупок за 100 мс (запас до порога №4 мал).",
                    evidence=f"Всего заявок по инструменту: {cnt}.",
                    action=f"Снизьте частоту по {code} или распределите заявки по времени.",
                )
            )

        if mc1 >= CRIT3_BURST:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_HIGH,
                    category="instrument",
                    title=f"Перегрев по {code} (1 с)",
                    message=f"{mc1} покупок за ≤1 с по одному инструменту.",
                    evidence=f"Серия с {_format_sec(st1)}.",
                    action=f"По {code}: не более {SAFE_MAX_IN_1S} заявок в секунду.",
                )
            )

    return tips


def _tips_limits(
    df: pd.DataFrame,
    instrument_limit: int,
    day_limit_c6: int,
) -> List[StrategyTip]:
    tips: List[StrategyTip] = []
    counts = instruments_order_counts(df)
    if counts.empty:
        return tips

    safe_inst = int(instrument_limit * 0.8)
    safe_day = int(day_limit_c6 * 0.8)

    for _, row in counts.iterrows():
        code = row["Код инструмента"]
        cnt = int(row["Количество"])
        if cnt > instrument_limit:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_HIGH,
                    category="limit",
                    title=f"Превышен лимит стакана: {code}",
                    message=f"{cnt} заявок при лимите {instrument_limit}.",
                    evidence="Подсчёт всех заявок по коду инструмента за день.",
                    action=(
                        f"На следующую сессию планируйте не более {safe_inst} заявок "
                        f"по одному инструменту."
                    ),
                )
            )
        elif cnt > instrument_limit * 0.85:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_MEDIUM,
                    category="limit",
                    title=f"Близко к лимиту стакана: {code}",
                    message=f"{cnt} из {instrument_limit} заявок ({cnt / instrument_limit * 100:.0f}%).",
                    evidence="Запас менее 15%.",
                    action=f"Целевой потолок: {safe_inst} заявок по {code}.",
                )
            )

    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    if not buys.empty and "Код инструмента" in buys.columns:
        buy_counts = buys.groupby("Код инструмента").size()
        for code, cnt in buy_counts.items():
            if cnt > day_limit_c6:
                tips.append(
                    StrategyTip(
                        priority=PRIORITY_HIGH,
                        category="limit",
                        title=f"Риск критерия №6: {code}",
                        message=f"{cnt} покупок за день (порог {day_limit_c6}).",
                        evidence="Подсчёт покупок по инструменту.",
                        action=f"Снизьте число покупок по {code} до ≤{safe_day} в день.",
                    )
                )
            elif cnt > day_limit_c6 * 0.85:
                tips.append(
                    StrategyTip(
                        priority=PRIORITY_MEDIUM,
                        category="limit",
                        title=f"Много покупок по {code}",
                        message=f"{cnt} покупок (порог №6: {day_limit_c6}).",
                        evidence="Приближение к дневному лимиту.",
                        action=f"Держите ≤{safe_day} покупок по инструменту.",
                    )
                )

    return tips


def _tips_corridor(df: pd.DataFrame) -> List[StrategyTip]:
    tips: List[StrategyTip] = []
    if "_above_corridor" not in df.columns:
        return tips

    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    if buys.empty:
        return tips

    above = buys[buys["_above_corridor"].fillna(False)]
    if above.empty:
        return tips

    share = len(above) / len(buys) * 100
    if share < 5:
        return tips

    priority = PRIORITY_HIGH if share >= 25 else PRIORITY_MEDIUM
    tips.append(
        StrategyTip(
            priority=priority,
            category="price",
            title="Частые заявки за верхней границей коридора",
            message=(
                f"{len(above)} из {len(buys)} покупок ({share:.1f}%) "
                "с превышением верхней границы."
            ),
            evidence="Колонка «Описание результата».",
            action=(
                "Поднимайте цену ступеньками с паузами; не выставляйте пачку "
                "одинаковых заявок подряд у границы коридора."
            ),
        )
    )

    if "Цена" in above.columns and len(above) >= 3:
        prices = above["Цена"].dropna().sort_values()
        if len(prices) >= 2:
            rapid = 0
            if "Время_сек" in above.columns:
                g = above.dropna(subset=["Время_сек", "Цена"]).sort_values("Время_сек")
                for i in range(1, len(g)):
                    if g.iloc[i]["Время_сек"] - g.iloc[i - 1]["Время_сек"] <= 1.0:
                        if g.iloc[i]["Цена"] > g.iloc[i - 1]["Цена"]:
                            rapid += 1
            if rapid >= 3:
                tips.append(
                    StrategyTip(
                        priority=PRIORITY_MEDIUM,
                        category="price",
                        title="Быстрое улучшение цены у границы",
                        message=(
                            f"{rapid} случаев повышения цены покупки за ≤1 с "
                            "у верхней границы коридора."
                        ),
                        evidence="Последовательные покупки с ростом цены.",
                        action=(
                            "Чередуйте паузы 1–2 с между шагами цены; "
                            "избегайте серий улучшения без сделки (критерий №2)."
                        ),
                    )
                )

    return tips


def _tips_from_criteria(results: List[CriterionResult]) -> List[StrategyTip]:
    tips: List[StrategyTip] = []
    actions = {
        1: "Между исполненными покупками выдерживайте ≥1 с; не более 2 за 1 с по инструменту.",
        2: "Не улучшайте цену покупки сериями без сделки — делайте паузы и подтверждайте сделку.",
        3: f"Не более {SAFE_MAX_IN_1S} покупок за 1 с; целевой интервал ≥{SAFE_MIN_GAP_1S} с.",
        4: f"Не более {SAFE_MAX_IN_100MS} покупок за 100 мс; интервал ≥{SAFE_MIN_GAP_MS} мс.",
        5: "У верхней границы коридора — меньше лотов и реже подача; следите за долей объёма.",
        6: "Распределите покупки по инструменту на день; не копите сотни заявок на один код.",
    }
    for r in results:
        if r.status == STATUS_VIOLATED:
            tips.append(
                StrategyTip(
                    priority=PRIORITY_HIGH,
                    category="criterion",
                    title=f"Критерий №{r.number} нарушен",
                    message=r.explanation[:300],
                    evidence=r.title,
                    action=actions.get(r.number, "Скорректируйте ритм и объёмы по методике биржи."),
                )
            )
    return tips


def _tips_positive(stats: Dict[str, Any], score: int) -> List[StrategyTip]:
    if score < 70:
        return []
    return [
        StrategyTip(
            priority=PRIORITY_LOW,
            category="positive",
            title="Ритм в безопасной зоне",
            message=(
                f"Индекс дисциплины {score}/100. "
                f"Макс. за 100 мс: {stats.get('max_100ms', 0)}, "
                f"за 1 с: {stats.get('max_1s', 0)}."
            ),
            evidence="Пороги №3–№4 не достигнуты с запасом.",
            action="Сохраняйте текущий темп; при ускорении не опускайтесь ниже 150 мс между заявками.",
        )
    ]


def _build_session_plan(tips: List[StrategyTip], stats: Dict[str, Any]) -> List[str]:
    """3–5 пунктов плана на следующую сессию."""
    plan: List[str] = []
    high = [t for t in tips if t.priority == PRIORITY_HIGH]
    for t in high[:3]:
        plan.append(t.action)

    if not plan:
        median = stats.get("gap_median_ms")
        if median is not None and median < SAFE_MIN_GAP_MS:
            plan.append(
                f"Увеличить типичную паузу между покупками с {median} мс до ≥{SAFE_MIN_GAP_MS} мс."
            )

    plan.append(
        f"Контрольные лимиты: не более {SAFE_MAX_IN_100MS} заявки / 100 мс, "
        f"не более {SAFE_MAX_IN_1S} за 1 с, пауза от {SAFE_MIN_GAP_MS} мс."
    )
    plan.append(
        "После сессии снова загрузите журнал и сравните индекс дисциплины с предыдущим днём."
    )

    # уникальные, максимум 5
    seen = set()
    unique: List[str] = []
    for p in plan:
        if p not in seen:
            seen.add(p)
            unique.append(p)
        if len(unique) >= 5:
            break
    return unique


def build_strategy_report(
    df: pd.DataFrame,
    results: List[CriterionResult],
    *,
    instrument_limit: int = 250,
    day_limit_c6: int = 500,
) -> StrategyReport:
    """Формирует полный отчёт советника."""
    stats = _rhythm_stats(df)
    score = _discipline_score(stats, instrument_limit, day_limit_c6, results, df)

    tips: List[StrategyTip] = []
    for fn in (
        lambda: _tip_rhythm_100ms(stats),
        lambda: _tip_rhythm_1s(stats),
        lambda: _tip_gap_distribution(stats),
        lambda: _tips_from_criteria(results),
        lambda: _tips_per_instrument(df),
        lambda: _tips_limits(df, instrument_limit, day_limit_c6),
        lambda: _tips_corridor(df),
        lambda: _tips_positive(stats, score),
    ):
        out = fn()
        if out is None:
            continue
        if isinstance(out, list):
            tips.extend(out)
        else:
            tips.append(out)

    tips.sort(key=lambda t: (PRIORITY_ORDER.get(t.priority, 9), t.category))

    return StrategyReport(
        discipline_score=score,
        rhythm_grade=_grade_from_score(score),
        tips=tips,
        session_plan=_build_session_plan(tips, stats),
        rhythm_stats=stats,
    )


def tips_to_dataframe(tips: List[StrategyTip]) -> pd.DataFrame:
    """Таблица рекомендаций для Streamlit."""
    if not tips:
        return pd.DataFrame(
            columns=["Приоритет", "Категория", "Заголовок", "Суть", "Действие"]
        )
    rows = []
    for t in tips:
        rows.append(
            {
                "Приоритет": PRIORITY_LABELS.get(t.priority, t.priority),
                "Категория": t.category,
                "Заголовок": t.title,
                "Суть": t.message,
                "Действие": t.action,
            }
        )
    return pd.DataFrame(rows)
