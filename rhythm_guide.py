"""
Визуальные шпаргалки по ритму подачи заявок (как нажимать кнопку покупки).
Отдельный модуль — чтобы Cloud всегда подтягивал графики независимо от кэша.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go

# Локальные константы (без импорта из strategy_advisor — нет циклов)
CRIT4_BURST = 3
SAFE_MAX_IN_100MS = 2
SAFE_MIN_GAP_MS = 150
SAFE_MAX_IN_1S = 5


def _compute_buy_gaps(buys: pd.DataFrame) -> pd.Series:
    """Интервалы между последовательными покупками (секунды)."""
    if buys.empty or "Время_сек" not in buys.columns:
        return pd.Series(dtype=float)
    times = buys["Время_сек"].dropna().sort_values()
    if len(times) < 2:
        return pd.Series(dtype=float)
    return times.diff().dropna()


def _timeline_trace(
    *,
    times_ms: List[float],
    y: float,
    name: str,
    color: str,
    marker_size: int = 16,
):
    """Точки-клики на горизонтальной шкале времени."""
    return go.Scatter(
        x=times_ms,
        y=[y] * len(times_ms),
        mode="markers+text",
        name=name,
        marker=dict(
            size=marker_size,
            color=color,
            symbol="circle",
            line=dict(width=1, color="#fff"),
        ),
        text=[f"{i + 1}" for i in range(len(times_ms))],
        textposition="top center",
        textfont=dict(size=11, color=color),
        hovertemplate="Клик %{text}<br>%{x:.0f} мс<extra></extra>",
    )


def fig_rhythm_howto_100ms():
    """Шпаргалка: 3 клика за 100 мс (плохо) vs пауза 200 мс (хорошо)."""
    fig = go.Figure()
    fig.add_vrect(x0=0, x1=100, fillcolor="#c0392b", opacity=0.12, line_width=0)
    fig.add_vrect(x0=100, x1=1000, fillcolor="#27ae60", opacity=0.06, line_width=0)

    bad = [0, 40, 85]
    fig.add_trace(
        _timeline_trace(times_ms=bad, y=2, name="Плохо: 3 клика за 100 мс", color="#c0392b")
    )
    fig.add_annotation(
        x=42,
        y=2.35,
        text="НАРУШЕНИЕ №4",
        showarrow=False,
        font=dict(size=11, color="#c0392b", family="Arial Black"),
    )

    good = [0, 200, 400, 600, 800]
    fig.add_trace(
        _timeline_trace(times_ms=good, y=1, name="Хорошо: пауза ~200 мс", color="#27ae60")
    )
    fig.add_annotation(
        x=100,
        y=1.35,
        text="200 мс",
        showarrow=False,
        font=dict(size=10, color="#1e8449"),
    )

    fig.add_vline(
        x=100,
        line_dash="dash",
        line_color="#c0392b",
        annotation_text="100 мс",
        annotation_position="top",
    )
    fig.update_layout(
        title="Как нажимать: окно 100 мс (критерий №4)",
        xaxis=dict(title="Время от первого клика, мс", range=[-30, 1050], tick0=0, dtick=100),
        yaxis=dict(
            range=[0.4, 2.8],
            tickvals=[1, 2],
            ticktext=["Правильно", "Неправильно"],
            showgrid=False,
        ),
        height=320,
        margin=dict(t=50, b=50, l=90, r=20),
        legend=dict(orientation="h", y=1.12),
        plot_bgcolor="#fafbfc",
    )
    return fig


def fig_rhythm_howto_1s():
    """Шпаргалка по окну 1 секунда (критерий №3)."""
    fig = go.Figure()
    fig.add_vrect(x0=0, x1=1000, fillcolor="#f39c12", opacity=0.08, line_width=0)

    bad = [i * 120 for i in range(8)]
    fig.add_trace(
        _timeline_trace(
            times_ms=bad,
            y=2,
            name="Плохо: 8 кликов за 1 с",
            color="#c0392b",
            marker_size=12,
        )
    )
    fig.add_annotation(
        x=500,
        y=2.4,
        text="НАРУШЕНИЕ №3 (от 7 за 1 с)",
        showarrow=False,
        font=dict(size=11, color="#c0392b"),
    )

    good = [0, 250, 500, 750]
    fig.add_trace(
        _timeline_trace(
            times_ms=good,
            y=1,
            name="Хорошо: 4 клика / с (пауза 250 мс)",
            color="#27ae60",
            marker_size=14,
        )
    )

    fig.add_vline(
        x=1000,
        line_dash="dash",
        line_color="#e67e22",
        annotation_text="1 с",
        annotation_position="top",
    )
    fig.update_layout(
        title="Как нажимать: окно 1 секунда (критерий №3)",
        xaxis=dict(title="Время от первого клика, мс", range=[-40, 1100], tick0=0, dtick=200),
        yaxis=dict(
            range=[0.4, 2.9],
            tickvals=[1, 2],
            ticktext=["Правильно", "Неправильно"],
            showgrid=False,
        ),
        height=320,
        margin=dict(t=50, b=50, l=90, r=20),
        legend=dict(orientation="h", y=1.12),
        plot_bgcolor="#fafbfc",
    )
    return fig


def fig_user_gaps_vs_safe(stats: Dict[str, Any], df: pd.DataFrame):
    """Гистограмма пауз пользователя относительно безопасной зоны."""
    buys = df[df["_is_buy"]] if "_is_buy" in df.columns else df.iloc[0:0]
    gaps = _compute_buy_gaps(buys)
    if gaps.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Недостаточно покупок для анализа пауз",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        fig.update_layout(height=280, xaxis=dict(visible=False), yaxis=dict(visible=False))
        return fig

    gaps_ms = (gaps * 1000).clip(upper=2000)
    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=gaps_ms,
            nbinsx=40,
            name="Ваши паузы",
            marker_color="#2E86AB",
            opacity=0.85,
        )
    )
    fig.add_vline(
        x=SAFE_MIN_GAP_MS,
        line_dash="dash",
        line_color="#27ae60",
        annotation_text=f"Цель от {SAFE_MIN_GAP_MS} мс",
        annotation_position="top right",
    )
    fig.add_vline(
        x=100,
        line_dash="dot",
        line_color="#c0392b",
        annotation_text="Опасно <100 мс",
        annotation_position="top left",
    )

    min_ms = stats.get("gap_min_ms")
    med_ms = stats.get("gap_median_ms")
    title_extra = ""
    if min_ms is not None and med_ms is not None:
        title_extra = f" (мин {min_ms} мс, медиана {med_ms} мс)"

    fig.update_layout(
        title=f"Ваши паузы между покупками{title_extra}",
        xaxis_title="Пауза между кликами, мс (обрезано до 2000)",
        yaxis_title="Количество",
        height=340,
        margin=dict(t=50, b=50, l=50, r=20),
        bargap=0.05,
        showlegend=False,
        plot_bgcolor="#fafbfc",
    )
    return fig


def fig_click_metronome():
    """Метроном безопасного темпа: клик каждые 200 мс."""
    times = list(range(0, 2001, 200))
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=times,
            y=[1] * len(times),
            mode="markers+text+lines",
            line=dict(color="#27ae60", width=2),
            marker=dict(size=18, color="#27ae60"),
            text=[f"{i + 1}" for i in range(len(times))],
            textposition="top center",
            name="Клик",
            hovertemplate="Клик %{text}<br>%{x} мс<extra></extra>",
        )
    )
    for i in range(len(times) - 1):
        mid = (times[i] + times[i + 1]) / 2
        fig.add_annotation(
            x=mid,
            y=0.75,
            text="жди",
            showarrow=False,
            font=dict(size=10, color="#7f8c8d"),
        )

    fig.update_layout(
        title="Метроном безопасного темпа: клик -> пауза 200 мс -> клик",
        xaxis=dict(title="Время, мс", range=[-50, 2100], tick0=0, dtick=200),
        yaxis=dict(visible=False, range=[0.5, 1.5]),
        height=220,
        margin=dict(t=50, b=40, l=20, r=20),
        showlegend=False,
        plot_bgcolor="#eafaf1",
    )
    return fig


def recommended_click_instruction(stats: Dict[str, Any]) -> Dict[str, str]:
    """Короткий текст «как нажимать» с учётом статистики пользователя."""
    min_ms = stats.get("gap_min_ms")
    max_100 = stats.get("max_100ms", 0)
    max_1s = stats.get("max_1s", 0)

    if max_100 >= CRIT4_BURST or (min_ms is not None and min_ms < 80):
        pace = (
            "Сейчас темп слишком быстрый. Считайте про себя: «раз — и — два» "
            "(примерно 200 мс) перед каждым следующим кликом покупки."
        )
        mode = "slow_down"
    elif max_100 >= SAFE_MAX_IN_100MS or max_1s >= SAFE_MAX_IN_1S:
        pace = (
            "Темп на грани. Не «долбите» кнопку: после клика сделайте паузу "
            f"не меньше {SAFE_MIN_GAP_MS} мс (удобно держать ритм раз в ~200–250 мс)."
        )
        mode = "caution"
    else:
        pace = (
            "Ритм в целом в норме. Сохраняйте паузу от 150–200 мс между покупками "
            "и не больше 4–5 кликов в секунду на одном инструменте."
        )
        mode = "ok"

    return {
        "mode": mode,
        "headline": "Как нажимать кнопку покупки",
        "pace": pace,
        "rule_short": (
            f"1) Не более 2 кликов за 100 мс.  "
            f"2) Пауза от {SAFE_MIN_GAP_MS} мс (лучше 200 мс).  "
            f"3) Не более {SAFE_MAX_IN_1S} кликов за 1 секунду."
        ),
        "count_trick": (
            "Простой приём: после клика тихо скажите «и-раз» — это около 200 мс. "
            "Только потом следующий клик. Не отправляйте 3 заявки «пачкой»."
        ),
    }
