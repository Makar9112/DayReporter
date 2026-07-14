"""
Проверка критериев недобросовестных торговых практик (СПбМТСБ).
Каждая функция возвращает словарь с результатом проверки.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import pandas as pd


# Результат: «Нарушен» / «Не нарушен» / «Исключение (Корзина)» / «Требуется доп. информация»
STATUS_VIOLATED = "Нарушен"
STATUS_OK = "Не нарушен"
STATUS_BASKET = "Исключение (Корзина)"
STATUS_INFO = "Требуется доп. информация"
STATUS_POTENTIAL = "Потенциальное нарушение"


@dataclass
class CriterionResult:
    """Результат проверки одного критерия."""

    number: int
    title: str
    status: str
    explanation: str
    details: Optional[List[str]] = None
    simplified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _max_burst_in_window(
    times_sec: pd.Series,
    window_sec: float,
) -> tuple:
    """
    Находит максимальное число событий в скользящем окне window_sec.
    Возвращает (max_count, example_start_sec).
    times_sec должен быть отсортирован по возрастанию.
    """
    vals = sorted(t for t in times_sec.dropna().tolist() if t is not None)
    n = len(vals)
    if n == 0:
        return 0, None

    max_count = 1
    example_start = vals[0]
    left = 0
    for right in range(n):
        while vals[right] - vals[left] > window_sec:
            left += 1
        count = right - left + 1
        if count > max_count:
            max_count = count
            example_start = vals[left]
    return max_count, example_start


def _format_sec(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def check_criterion_1(df: pd.DataFrame) -> CriterionResult:
    """
    №1: Систематическое выставление заявок на покупку, приводящих к сделке,
    с интервалом < 1 с, начиная с 3-й заявки по одному инструменту.

    Упрощённая проверка (нет встречных заявок): среди исполненных покупок
    по инструменту — есть ли >= 3 заявок в окне 1 секунды.
    """
    title = (
        "Систематическое выставление исполненных заявок на покупку "
        "с интервалом ≤ 1 с (начиная с 3-й)"
    )

    if df.empty:
        return CriterionResult(1, title, STATUS_OK, "Нет данных для проверки.")

    buys = df[df["_is_buy"] & df["_is_executed"]].copy()
    if buys.empty:
        return CriterionResult(
            1,
            title,
            STATUS_OK,
            "Исполненных заявок на покупку не найдено — критерий не нарушен "
            "(упрощённая проверка без данных о встречных заявках).",
            simplified=True,
        )

    violations: List[str] = []
    for code, group in buys.groupby("Код инструмента"):
        times = group["Время_сек"].dropna().sort_values()
        max_count, start = _max_burst_in_window(times, 1.0)
        # Начиная с 3-й → порог > 2
        if max_count >= 3:
            violations.append(
                f"{code}: {max_count} исполненных покупок за ≤1 с "
                f"(начиная с {_format_sec(start)})"
            )

    if violations:
        return CriterionResult(
            1,
            title,
            STATUS_POTENTIAL,
            "Обнаружены серии исполненных заявок на покупку с интервалом < 1 с "
            "(≥ 3 шт.). Упрощённая проверка — требуется подтверждение по встречным заявкам.",
            details=violations,
            simplified=True,
        )

    return CriterionResult(
        1,
        title,
        STATUS_OK,
        "Серий из ≥ 3 исполненных покупок за 1 с по одному инструменту не обнаружено "
        "(упрощённая проверка).",
        simplified=True,
    )


def check_criterion_2(df: pd.DataFrame) -> CriterionResult:
    """
    №2: Систематическое выставление покупок, не приводящих к сделке,
    но улучшающих лучшую цену, с интервалом ≤ 1 с, начиная с 7-й.

    Упрощённо: покупки без исполнения, где цена выше предыдущей по инструменту,
    интервал < 1 с, серия ≥ 7.
    """
    title = (
        "Систематическое улучшение лучшей цены покупки без сделки "
        "с интервалом ≤ 1 с (начиная с 7-й)"
    )

    if df.empty:
        return CriterionResult(2, title, STATUS_OK, "Нет данных для проверки.")

    # Заявки без сделки
    not_exec = df[df["_is_buy"] & ~df["_is_executed"]].copy()
    if not_exec.empty or "Цена" not in not_exec.columns:
        return CriterionResult(
            2,
            title,
            STATUS_OK,
            "Неисполненных заявок на покупку недостаточно для проверки "
            "(упрощённая проверка без данных о лучшей цене стакана).",
            simplified=True,
        )

    violations: List[str] = []
    for code, group in not_exec.groupby("Код инструмента"):
        g = group.dropna(subset=["Время_сек", "Цена"]).sort_values("Время_сек")
        if len(g) < 7:
            continue

        # Считаем подряд идущие улучшения цены с интервалом < 1 с
        times = g["Время_сек"].tolist()
        prices = g["Цена"].tolist()
        streak = 1
        max_streak = 1
        streak_start = times[0]

        for i in range(1, len(times)):
            dt = times[i] - times[i - 1]
            improved = prices[i] > prices[i - 1]
            if improved and dt <= 1.0:
                streak += 1
                if streak > max_streak:
                    max_streak = streak
            else:
                streak = 1
                streak_start = times[i]

        if max_streak >= 7:
            violations.append(
                f"{code}: серия из {max_streak} улучшений цены за ≤1 с "
                f"(около {_format_sec(streak_start)})"
            )

    if violations:
        return CriterionResult(
            2,
            title,
            STATUS_POTENTIAL,
            "Обнаружены серии улучшений цены покупки без сделки (≥ 7 за ≤1 с). "
            "Упрощённая проверка — нет данных о текущей лучшей цене стакана.",
            details=violations,
            simplified=True,
        )

    # Дополнительно: частые неисполненные покупки без улучшения цены
    burst_notes: List[str] = []
    for code, group in not_exec.groupby("Код инструмента"):
        times = group["Время_сек"].dropna().sort_values()
        max_count, start = _max_burst_in_window(times, 1.0)
        if max_count >= 7:
            burst_notes.append(
                f"{code}: {max_count} неисполненных покупок за ≤1 с "
                f"(с {_format_sec(start)}) — улучшения цены в серии не подтверждены"
            )

    if burst_notes:
        return CriterionResult(
            2,
            title,
            STATUS_INFO,
            "Есть частые неисполненные покупки (< 1 с, ≥ 7), но улучшение лучшей цены "
            "стакана по данным файла однозначно не подтверждено. Требуется доп. информация.",
            details=burst_notes,
            simplified=True,
        )

    return CriterionResult(
        2,
        title,
        STATUS_OK,
        "Серий из ≥ 7 улучшений цены покупки за 1 с не обнаружено (упрощённая проверка).",
        simplified=True,
    )


def _burst_check_buys(
    df: pd.DataFrame,
    *,
    number: int,
    title: str,
    window_sec: float,
    threshold: int,
    window_label: str,
    use_basket: bool,
) -> CriterionResult:
    """
    Общая логика критериев №3 и №4: скользящее окно по покупкам.
    При «Корзине» статус — исключение, но фактический результат проверки
    всё равно попадает в пояснение (чтобы не скрывать нарушение).
    """
    if df.empty:
        factual = CriterionResult(number, title, STATUS_OK, "Нет данных для проверки.")
        return _apply_basket_exception(factual, use_basket)

    buys = df[df["_is_buy"]].copy() if "_is_buy" in df.columns else df.iloc[0:0].copy()
    if buys.empty:
        factual = CriterionResult(number, title, STATUS_OK, "Заявок на покупку не найдено.")
        return _apply_basket_exception(factual, use_basket)

    times_all = buys["Время_сек"].dropna().sort_values()
    max_all, start_all = _max_burst_in_window(times_all, window_sec)

    details: List[str] = []
    for code, group in buys.groupby("Код инструмента"):
        mc, st = _max_burst_in_window(group["Время_сек"], window_sec)
        if mc >= threshold:
            details.append(
                f"{code}: {mc} покупок за ≤{window_label} (с {_format_sec(st)})"
            )

    violated = max_all >= threshold or bool(details)
    if violated:
        factual = CriterionResult(
            number,
            title,
            STATUS_VIOLATED,
            (
                f"Максимум покупок за {window_label} по всей сессии: {max_all} "
                f"(с {_format_sec(start_all)}). Порог нарушения: ≥ {threshold}."
            ),
            details=details or None,
        )
    else:
        factual = CriterionResult(
            number,
            title,
            STATUS_OK,
            (
                f"Максимум заявок на покупку за {window_label}: {max_all} "
                f"(< {threshold}). Критерий не нарушен."
            ),
        )
    return _apply_basket_exception(factual, use_basket)


def _apply_basket_exception(
    factual: CriterionResult,
    use_basket: bool,
) -> CriterionResult:
    """Если включена корзина — статус исключения, но сохраняем факт проверки."""
    if not use_basket:
        return factual

    note = (
        "Включён режим «Корзина заявок» — по документации критерий не применяется "
        "(исключение). "
    )
    if factual.status == STATUS_VIOLATED:
        note += (
            "Важно: по данным файла порог фактически превышен. "
            f"{factual.explanation}"
        )
    else:
        note += f"Фактическая проверка: {factual.explanation}"

    return CriterionResult(
        factual.number,
        factual.title,
        STATUS_BASKET,
        note,
        details=factual.details,
        simplified=factual.simplified,
    )


def check_criterion_3(df: pd.DataFrame, use_basket: bool = False) -> CriterionResult:
    """
    №3: Выставление заявок на покупку за ≤ 1 с, начиная с 7-й
    (одним уполномоченным лицом / в рамках сессии).
    При «Корзине заявок» — исключение (факт проверки всё равно показывается).
    """
    return _burst_check_buys(
        df,
        number=3,
        title="Выставление ≥ 7 заявок на покупку за период ≤ 1 с в рамках сессии",
        window_sec=1.0,
        threshold=7,
        window_label="1 с",
        use_basket=use_basket,
    )


def check_criterion_4(df: pd.DataFrame, use_basket: bool = False) -> CriterionResult:
    """
    №4: Выставление заявок на покупку за ≤ 100 мс, начиная с 3-й.
    При «Корзине заявок» — исключение (факт проверки всё равно показывается).
    """
    return _burst_check_buys(
        df,
        number=4,
        title="Выставление ≥ 3 заявок на покупку за период ≤ 100 мс в рамках сессии",
        window_sec=0.1,
        threshold=3,
        window_label="100 мс",
        use_basket=use_basket,
    )


def check_criterion_5(df: pd.DataFrame, use_basket: bool = False) -> CriterionResult:
    """
    №5: Покупки по цене = Верхний предел допустимой цены, за ≤ 1 с после изменения
    рыночной цены, объём > 5 лотов (франко-вагон) / > 3 (франко-труба) и ≥ 25%
    от объёма торгов по инструменту за день.

    Упрощение: общее объём торгов ≈ сумма объёмов всех заявок по инструменту.
    Исполненные сделки важны; если исполненных нет — обычно «Не нарушен».
    При «Корзине» — исключение, но факт проверки показывается.
    """
    title = (
        "Покупки по верхней границе коридора с объёмом > 5 лотов (вагон) "
        "и ≥ 25% дневного объёма по инструменту"
    )

    if df.empty:
        return _apply_basket_exception(
            CriterionResult(5, title, STATUS_OK, "Нет данных для проверки."),
            use_basket,
        )

    volume_col = "Объем, лотов" if "Объем, лотов" in df.columns else None
    if volume_col is None:
        return _apply_basket_exception(
            CriterionResult(
                5,
                title,
                STATUS_INFO,
                "В файле нет колонки «Объем, лотов» — проверка невозможна.",
            ),
            use_basket,
        )

    near_cap = df[df["_is_buy"]].copy()
    if near_cap.empty:
        return _apply_basket_exception(
            CriterionResult(5, title, STATUS_OK, "Заявок на покупку не найдено."),
            use_basket,
        )

    eps = 1e-6
    near_cap["_at_cap"] = False
    if "_corridor_bound" in near_cap.columns and "Цена" in near_cap.columns:
        near_cap["_at_cap"] = (
            near_cap["_corridor_bound"].notna()
            & near_cap["Цена"].notna()
            & ((near_cap["Цена"] - near_cap["_corridor_bound"]).abs() <= eps)
        )
    if "_above_corridor" in near_cap.columns:
        near_cap["_at_cap"] = near_cap["_at_cap"] | near_cap["_above_corridor"].fillna(False)

    executed_at_cap = near_cap[near_cap["_at_cap"] & near_cap["_is_executed"]]
    if executed_at_cap.empty:
        any_at_cap = near_cap[near_cap["_at_cap"]]
        note = ""
        if not any_at_cap.empty:
            note = (
                f" Найдено {len(any_at_cap)} заявок у верхней границы, "
                f"но ни одна не исполнена — приобретения лотов нет."
            )
        factual = CriterionResult(
            5,
            title,
            STATUS_OK,
            "Исполненных покупок по верхней границе коридора не обнаружено."
            + note
            + " (общий объём торгов приближён суммой объёмов всех заявок).",
        )
        return _apply_basket_exception(factual, use_basket)

    violations: List[str] = []
    warnings = (
        "Внимание: общий объём торгов по инструменту приближён как сумма объёмов "
        "всех заявок (фактический объём реализации в файле отсутствует)."
    )

    for code, group_all in df.groupby("Код инструмента"):
        total_vol = float(group_all[volume_col].fillna(0).sum())
        if total_vol <= 0:
            continue

        cap_exec = executed_at_cap[executed_at_cap["Код инструмента"] == code]
        if cap_exec.empty:
            continue

        cap_vol = float(cap_exec[volume_col].fillna(0).sum())
        basis = ""
        if "Базис" in group_all.columns and len(group_all):
            basis = str(group_all["Базис"].iloc[0])

        lot_threshold = 3 if "труба" in basis.lower() else 5
        share = cap_vol / total_vol * 100

        if cap_vol > lot_threshold and share >= 25.0:
            violations.append(
                f"{code} ({basis or 'базис н/д'}): объём по верхней границе "
                f"{cap_vol:.0f} лотов ({share:.1f}% от {total_vol:.0f}), "
                f"порог лотов > {lot_threshold}"
            )

    if violations:
        factual = CriterionResult(
            5,
            title,
            STATUS_VIOLATED,
            warnings + " Обнаружено превышение порогов по объёму у верхней границы.",
            details=violations,
        )
    else:
        factual = CriterionResult(
            5,
            title,
            STATUS_OK,
            warnings
            + " Пороги по объёму / доле 25% не превышены для исполненных покупок "
            "у верхней границы.",
        )
    return _apply_basket_exception(factual, use_basket)


def check_criterion_6(df: pd.DataFrame, day_limit: int = 500) -> CriterionResult:
    """
    №6: > 500 зафиксированных (но не зарегистрированных) заявок на покупку
    по одному инструменту (бензин/дизель) за торговый день
    (месячный порог 3500 без месячных данных не проверяем).

    Упрощение: считаем все заявки на покупку по инструменту за день.
    """
    title = (
        f"Превышение {day_limit} заявок на покупку по одному инструменту за торговый день"
    )

    if df.empty:
        return CriterionResult(6, title, STATUS_OK, "Нет данных для проверки.")

    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df
    if buys.empty:
        return CriterionResult(6, title, STATUS_OK, "Заявок на покупку не найдено.")

    counts = buys.groupby("Код инструмента").size().sort_values(ascending=False)
    over = counts[counts > day_limit]

    details = [f"{code}: {cnt} заявок" for code, cnt in over.items()]
    max_code = counts.index[0]
    max_cnt = int(counts.iloc[0])

    if len(over):
        return CriterionResult(
            6,
            title,
            STATUS_VIOLATED,
            f"По {len(over)} инструмент(ам) количество заявок на покупку "
            f"превышает {day_limit} за день. Месячный порог (3500) не проверялся "
            f"(данные за один день).",
            details=details,
        )

    return CriterionResult(
        6,
        title,
        STATUS_OK,
        f"Максимум заявок на покупку по инструменту: {max_code} — {max_cnt} "
        f"(лимит {day_limit}). Месячный порог не проверялся.",
    )


def burst_diagnostics(df: pd.DataFrame) -> Dict[str, Any]:
    """Диагностика частоты покупок для UI (окна 100 мс и 1 с)."""
    if df.empty or "_is_buy" not in df.columns:
        return {
            "max_100ms": 0,
            "start_100ms": None,
            "max_1s": 0,
            "start_1s": None,
        }
    buys = df[df["_is_buy"]]
    times = buys["Время_сек"].dropna() if "Время_сек" in buys.columns else pd.Series(dtype=float)
    max_100, start_100 = _max_burst_in_window(times, 0.1)
    max_1s, start_1s = _max_burst_in_window(times, 1.0)
    return {
        "max_100ms": max_100,
        "start_100ms": start_100,
        "max_1s": max_1s,
        "start_1s": start_1s,
    }


def run_all_checks(
    df: pd.DataFrame,
    use_basket: bool = False,
    day_limit_c6: int = 500,
) -> List[CriterionResult]:
    """Запускает проверку всех шести критериев."""
    return [
        check_criterion_1(df),
        check_criterion_2(df),
        check_criterion_3(df, use_basket=use_basket),
        check_criterion_4(df, use_basket=use_basket),
        check_criterion_5(df, use_basket=use_basket),
        check_criterion_6(df, day_limit=day_limit_c6),
    ]


def results_to_dataframe(results: List[CriterionResult]) -> pd.DataFrame:
    """Преобразует список результатов в таблицу для отображения."""
    rows = []
    for r in results:
        rows.append(
            {
                "№": r.number,
                "Критерий": r.title,
                "Результат": r.status,
                "Пояснение": r.explanation,
                "Упрощённая проверка": "Да" if r.simplified else "Нет",
            }
        )
    return pd.DataFrame(rows)


def build_html_report(
    summary: Dict[str, Any],
    results: List[CriterionResult],
    limit_violations: pd.DataFrame,
    instrument_limit: int,
) -> str:
    """Формирует простой HTML-отчёт для скачивания."""
    status_color = {
        STATUS_VIOLATED: "#c0392b",
        STATUS_POTENTIAL: "#d35400",
        STATUS_OK: "#27ae60",
        STATUS_BASKET: "#f39c12",
        STATUS_INFO: "#2980b9",
    }

    rows_html = ""
    for r in results:
        color = status_color.get(r.status, "#333")
        details = ""
        if r.details:
            details = "<ul>" + "".join(f"<li>{d}</li>" for d in r.details) + "</ul>"
        rows_html += f"""
        <tr>
          <td>{r.number}</td>
          <td>{r.title}</td>
          <td style="color:{color};font-weight:bold">{r.status}</td>
          <td>{r.explanation}{details}</td>
        </tr>
        """

    limit_html = "<p>Превышений лимита не обнаружено.</p>"
    if limit_violations is not None and not limit_violations.empty:
        items = "".join(
            f"<li>{row['Код инструмента']}: {row['Количество']} заявок</li>"
            for _, row in limit_violations.iterrows()
        )
        limit_html = f"<ul>{items}</ul>"

    status_lines = "".join(
        f"<li>{k}: {v}</li>" for k, v in summary.get("status_counts", {}).items()
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <title>Отчёт — Анализатор торгов СПбМТСБ</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
    h1 {{ color: #1a5276; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #ccc; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #eaf2f8; }}
    .meta {{ background: #f8f9fa; padding: 12px 16px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>Отчёт анализатора торгов СПбМТСБ</h1>
  <div class="meta">
    <p><b>Всего заявок:</b> {summary.get('total', 0)}</p>
    <p><b>Инструментов:</b> {summary.get('instruments', 0)}</p>
    <p><b>Время:</b> {summary.get('time_min', '—')} — {summary.get('time_max', '—')}</p>
    <p><b>Общий объём, лотов:</b> {summary.get('total_volume', 0):.2f}</p>
    <p><b>Средний объём, лотов:</b> {summary.get('avg_volume', 0):.2f}</p>
    <p><b>Статусы:</b></p>
    <ul>{status_lines}</ul>
  </div>
  <h2>Проверка критериев</h2>
  <table>
    <thead>
      <tr><th>№</th><th>Критерий</th><th>Результат</th><th>Пояснение</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <h2>Лимит заявок на инструмент ({instrument_limit})</h2>
  {limit_html}
  <p style="margin-top:24px;color:#888;font-size:12px;">
    Сформировано автоматически. Критерии 1–2 — упрощённая проверка без данных о встречных заявках и стакане.
  </p>
</body>
</html>
"""
