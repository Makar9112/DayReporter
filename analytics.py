"""
Модуль аналитики и визуализации для анализатора торгов СПбМТСБ.
Статистика, графики Plotly, таблицы.
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from utils import format_timedelta


def compute_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Краткая сводка по загруженному файлу."""
    total = len(df)
    status_counts = df["Статус"].value_counts().to_dict() if "Статус" in df.columns else {}

    volume_col = "Объем, лотов" if "Объем, лотов" in df.columns else None
    total_volume = float(df[volume_col].sum()) if volume_col else 0.0
    avg_volume = float(df[volume_col].mean()) if volume_col and total else 0.0

    times = df["Время_td"].dropna() if "Время_td" in df.columns else pd.Series(dtype=object)
    time_min = format_timedelta(times.min()) if len(times) else "—"
    time_max = format_timedelta(times.max()) if len(times) else "—"

    instruments = df["Код инструмента"].nunique() if "Код инструмента" in df.columns else 0

    return {
        "total": total,
        "status_counts": status_counts,
        "total_volume": total_volume,
        "avg_volume": avg_volume,
        "time_min": time_min,
        "time_max": time_max,
        "instruments": instruments,
    }


def status_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Таблица распределения по статусам с объёмами."""
    if df.empty:
        return pd.DataFrame(columns=["Статус", "Количество", "Объем, лотов"])

    volume_col = "Объем, лотов" if "Объем, лотов" in df.columns else None
    if volume_col:
        grouped = (
            df.groupby("Статус", dropna=False)
            .agg(Количество=("Статус", "size"), **{"Объем, лотов": (volume_col, "sum")})
            .reset_index()
            .sort_values("Количество", ascending=False)
        )
    else:
        grouped = (
            df.groupby("Статус", dropna=False)
            .size()
            .reset_index(name="Количество")
            .sort_values("Количество", ascending=False)
        )
    return grouped


def basis_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Распределение заявок по базисам поставки."""
    if "Базис" not in df.columns or df.empty:
        return pd.DataFrame(columns=["Базис", "Количество", "Доля, %"])

    counts = df["Базис"].value_counts().reset_index()
    counts.columns = ["Базис", "Количество"]
    total = counts["Количество"].sum()
    counts["Доля, %"] = (counts["Количество"] / total * 100).round(1) if total else 0
    return counts


def fig_basis_pie(df: pd.DataFrame) -> go.Figure:
    """Круговая диаграмма по базисам."""
    data = basis_distribution(df)
    if data.empty:
        return _empty_fig("Нет данных по базисам")

    fig = px.pie(
        data,
        names="Базис",
        values="Количество",
        title="Распределение по базисам поставки",
        hole=0.35,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(legend_title_text="Базис", margin=dict(t=50, b=20, l=20, r=20))
    return fig


def fig_status_pie(df: pd.DataFrame) -> go.Figure:
    """Круговая диаграмма статусов."""
    if df.empty or "Статус" not in df.columns:
        return _empty_fig("Нет данных по статусам")

    counts = df["Статус"].value_counts().reset_index()
    counts.columns = ["Статус", "Количество"]
    fig = px.pie(
        counts,
        names="Статус",
        values="Количество",
        title="Распределение заявок по статусам",
        hole=0.35,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(legend_title_text="Статус", margin=dict(t=50, b=20, l=20, r=20))
    return fig


def fig_top_instruments(df: pd.DataFrame, top_n: int = 10) -> go.Figure:
    """Столбчатая диаграмма топ-N инструментов по количеству заявок."""
    if df.empty or "Код инструмента" not in df.columns:
        return _empty_fig("Нет данных по инструментам")

    counts = (
        df.groupby("Код инструмента")
        .size()
        .reset_index(name="Количество")
        .sort_values("Количество", ascending=False)
        .head(top_n)
    )

    # Добавим короткое наименование, если есть
    if "Наименование инструмента" in df.columns:
        names = (
            df.groupby("Код инструмента")["Наименование инструмента"]
            .agg(lambda s: s.dropna().iloc[0] if len(s.dropna()) else "")
        )
        counts["Наименование"] = counts["Код инструмента"].map(names)
        counts["Подпись"] = counts["Код инструмента"] + "<br>" + counts["Наименование"].fillna("").str[:40]
    else:
        counts["Подпись"] = counts["Код инструмента"]

    fig = px.bar(
        counts,
        x="Код инструмента",
        y="Количество",
        title=f"Топ-{top_n} инструментов по количеству заявок",
        text="Количество",
        hover_data=[c for c in ("Наименование",) if c in counts.columns],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        xaxis_title="Код инструмента",
        yaxis_title="Количество заявок",
        margin=dict(t=50, b=80, l=40, r=20),
    )
    return fig


def fig_hourly_distribution(df: pd.DataFrame) -> go.Figure:
    """
    Гистограмма распределения заявок по времени.
    Группировка по часам и минутам (бинамы по минутам внутри сессии).
    """
    if df.empty or "Время_сек" not in df.columns:
        return _empty_fig("Нет данных о времени")

    valid = df.dropna(subset=["Время_сек"]).copy()
    if valid.empty:
        return _empty_fig("Не удалось распознать время фиксации")

    # Группировка по минутам: час + минута
    valid["Минута_сессии"] = (valid["Время_сек"] // 60).astype(int)
    valid["Подпись"] = valid["Минута_сессии"].map(
        lambda m: f"{m // 60:02d}:{m % 60:02d}"
    )

    grouped = (
        valid.groupby(["Минута_сессии", "Подпись"])
        .size()
        .reset_index(name="Количество")
        .sort_values("Минута_сессии")
    )

    # Также агрегат по часам для второй оси/подсказки
    valid["Час"] = (valid["Время_сек"] // 3600).astype(int)
    by_hour = valid.groupby("Час").size().reset_index(name="Количество")
    by_hour["Подпись"] = by_hour["Час"].map(lambda h: f"{h:02d}:00")

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=grouped["Подпись"],
            y=grouped["Количество"],
            name="По минутам",
            marker_color="#2E86AB",
        )
    )
    fig.update_layout(
        title="Распределение заявок по времени (группировка по минутам)",
        xaxis_title="Время (ЧЧ:ММ)",
        yaxis_title="Количество заявок",
        margin=dict(t=50, b=80, l=40, r=20),
        xaxis=dict(tickangle=-45, nticks=min(40, len(grouped))),
    )

    # Вспомогательная аннотация по часам
    hour_text = ", ".join(f"{r['Подпись']}: {r['Количество']}" for _, r in by_hour.iterrows())
    fig.add_annotation(
        text=f"По часам: {hour_text}" if hour_text else "",
        xref="paper",
        yref="paper",
        x=0,
        y=1.08,
        showarrow=False,
        font=dict(size=11, color="#555"),
        align="left",
    )
    return fig


def fig_corridor_deviations(df: pd.DataFrame) -> go.Figure:
    """
    Гистограмма отклонений цены от верхней границы коридора.
    Для исполненных/снятых — «в пределах коридора» (отклонение 0 не включаем в гистограмму,
    но показываем счётчик).
    """
    if df.empty:
        return _empty_fig("Нет данных")

    work = df.copy()
    work["Отклонение"] = None

    # Заявки за верхней границей
    mask_above = work["_above_corridor"].fillna(False) & work["_corridor_bound"].notna()
    if "Цена" in work.columns:
        work.loc[mask_above, "Отклонение"] = (
            work.loc[mask_above, "Цена"] - work.loc[mask_above, "_corridor_bound"]
        )

    # Исполненные / снятые без нарушения коридора — считаем «в пределах»
    within_mask = (
        ~work["_above_corridor"].fillna(False)
        & (
            work["Статус"].astype(str).str.lower().str.contains("исполнен", na=False)
            | work["Статус"].astype(str).str.lower().str.contains("снят", na=False)
        )
    )
    within_count = int(within_mask.sum())
    above_count = int(mask_above.sum())

    deviations = work.loc[mask_above, "Отклонение"].dropna()

    if deviations.empty:
        fig = go.Figure()
        fig.add_annotation(
            text=(
                f"Нет заявок за верхней границей коридора.<br>"
                f"В пределах коридора (исполн./сняты): {within_count}"
            ),
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=14),
        )
        fig.update_layout(
            title="Отклонения от верхней границы ценового коридора",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            margin=dict(t=50, b=20, l=20, r=20),
        )
        return fig

    fig = px.histogram(
        deviations,
        nbins=min(30, max(5, len(deviations))),
        title="Отклонения цены от верхней границы коридора",
        labels={"value": "Цена − граница коридора", "count": "Количество"},
    )
    fig.update_layout(
        xaxis_title="Отклонение (цена − граница)",
        yaxis_title="Количество заявок",
        showlegend=False,
        margin=dict(t=70, b=40, l=40, r=20),
        annotations=[
            dict(
                text=f"За коридором: {above_count} | В пределах (исполн./сняты): {within_count}",
                xref="paper",
                yref="paper",
                x=0,
                y=1.12,
                showarrow=False,
                font=dict(size=11, color="#555"),
            )
        ],
    )
    return fig


def instruments_order_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Количество заявок по каждому инструменту."""
    if df.empty or "Код инструмента" not in df.columns:
        return pd.DataFrame(columns=["Код инструмента", "Наименование", "Количество"])

    if "Наименование инструмента" in df.columns:
        grouped = (
            df.groupby("Код инструмента")
            .agg(
                Количество=("Код инструмента", "size"),
                Наименование=("Наименование инструмента", "first"),
            )
            .reset_index()
            .sort_values("Количество", ascending=False)
        )
    else:
        grouped = (
            df.groupby("Код инструмента")
            .size()
            .reset_index(name="Количество")
            .sort_values("Количество", ascending=False)
        )
        grouped["Наименование"] = ""
    return grouped


def fig_instruments_limit(df: pd.DataFrame, limit: int = 250) -> go.Figure:
    """Гистограмма количества заявок по инструментам с выделением превышений лимита."""
    counts = instruments_order_counts(df)
    if counts.empty:
        return _empty_fig("Нет данных по инструментам")

    counts = counts.copy()
    counts["Превышение"] = counts["Количество"] > limit
    counts["Цвет"] = counts["Превышение"].map({True: "#C0392B", False: "#27AE60"})

    fig = go.Figure(
        go.Bar(
            x=counts["Код инструмента"],
            y=counts["Количество"],
            marker_color=counts["Цвет"],
            text=counts["Количество"],
            textposition="outside",
            hovertemplate=(
                "%{x}<br>Заявок: %{y}"
                + ("<br>%{customdata}" if "Наименование" in counts.columns else "")
                + "<extra></extra>"
            ),
            customdata=counts["Наименование"] if "Наименование" in counts.columns else None,
        )
    )
    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color="#E67E22",
        annotation_text=f"Лимит: {limit}",
        annotation_position="top left",
    )
    fig.update_layout(
        title="Количество заявок по инструментам (лимит на стакан)",
        xaxis_title="Код инструмента",
        yaxis_title="Количество заявок",
        margin=dict(t=50, b=80, l=40, r=20),
        xaxis=dict(tickangle=-45),
    )
    return fig


def check_instrument_limits(df: pd.DataFrame, limit: int = 250) -> pd.DataFrame:
    """Возвращает инструменты, превысившие лимит заявок."""
    counts = instruments_order_counts(df)
    if counts.empty:
        return counts
    return counts[counts["Количество"] > limit].copy()


def _empty_fig(message: str) -> go.Figure:
    """Пустой график с текстовым сообщением."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=14, color="#888"),
    )
    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(t=40, b=20, l=20, r=20),
        height=320,
    )
    return fig
