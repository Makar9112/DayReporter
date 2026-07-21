"""
Модуль аналитики и визуализации для анализатора торгов СПбМТСБ.
Статистика, графики Plotly, таблицы.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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


def _merge_orders_and_basis_fill(
    df: pd.DataFrame,
    fill_by_inst: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Объединяет счётчики заявок и залив по договорам для общего графика."""
    counts = instruments_order_counts(df)
    if fill_by_inst is None or fill_by_inst.empty:
        counts["Договоров"] = 0
        counts["Лоты"] = 0.0
        counts["Тонны залива"] = 0.0
        return counts

    fill = fill_by_inst[
        ["Код инструмента", "Договоров", "Лоты", "Тонны залива"]
    ].copy()
    fill["Код инструмента"] = fill["Код инструмента"].astype(str)

    if counts.empty:
        merged = fill.copy()
        merged["Количество"] = 0
        merged["Превышение"] = False
        if "Наименование" not in merged.columns:
            merged["Наименование"] = ""
    else:
        counts = counts.copy()
        counts["Код инструмента"] = counts["Код инструмента"].astype(str)
        merged = counts.merge(fill, on="Код инструмента", how="outer")
        if "Наименование" not in merged.columns:
            merged["Наименование"] = ""
        name_fill = fill_by_inst[["Код инструмента", "Наименование"]].copy()
        name_fill["Код инструмента"] = name_fill["Код инструмента"].astype(str)
        merged = merged.merge(
            name_fill.rename(columns={"Наименование": "_name_fill"}),
            on="Код инструмента",
            how="left",
        )
        merged["Наименование"] = merged.apply(
            lambda r: r["Наименование"]
            if pd.notna(r.get("Наименование")) and str(r["Наименование"]).strip()
            else (r.get("_name_fill") or ""),
            axis=1,
        )
        merged.drop(columns=["_name_fill"], errors="ignore", inplace=True)

    merged["Количество"] = pd.to_numeric(merged.get("Количество"), errors="coerce").fillna(0).astype(int)
    merged["Договоров"] = pd.to_numeric(merged.get("Договоров"), errors="coerce").fillna(0).astype(int)
    merged["Лоты"] = pd.to_numeric(merged.get("Лоты"), errors="coerce").fillna(0.0)
    merged["Тонны залива"] = pd.to_numeric(merged.get("Тонны залива"), errors="coerce").fillna(0.0)
    merged = merged.sort_values(
        ["Количество", "Тонны залива"],
        ascending=[False, False],
    ).reset_index(drop=True)
    return merged


def fig_instruments_limit_with_basis_fill(
    df: pd.DataFrame,
    fill_by_inst: Optional[pd.DataFrame],
    limit: int = 250,
    *,
    variant: str = "dual_bars",
) -> go.Figure:
    """
    Лимиты заявок + залив базиса (тонны по договорам).

    variant:
      - dual_bars — столбики заявок и столбики тонн на второй оси Y
      - bars_line — столбики заявок, линия тонн на второй оси Y
      - grouped — рядом столбики «заявки, шт» и «вагоны, лот»
    """
    merged = _merge_orders_and_basis_fill(df, fill_by_inst)
    if merged.empty:
        return _empty_fig("Нет данных по инструментам")

    has_fill = (
        fill_by_inst is not None
        and not fill_by_inst.empty
        and float(merged["Тонны залива"].sum()) > 0
    )

    merged = merged.copy()
    merged["Превышение"] = merged["Количество"] > limit
    order_colors = merged["Превышение"].map({True: "#C0392B", False: "#27AE60"})

    codes = merged["Код инструмента"].astype(str)
    names = merged["Наименование"].fillna("").astype(str)

    if not has_fill:
        return fig_instruments_limit(df, limit=limit)

    fig = go.Figure()

    if variant == "grouped":
        fig.add_trace(
            go.Bar(
                name="Ваши заявки, шт",
                x=codes,
                y=merged["Количество"],
                marker_color=order_colors,
                text=merged["Количество"],
                textposition="outside",
                offsetgroup="orders",
                hovertemplate="%{x}<br>Заявок: %{y}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Bar(
                name="Залив (вагоны, лот)",
                x=codes,
                y=merged["Лоты"],
                marker_color="#3498DB",
                text=merged["Лоты"].map(lambda v: f"{v:.0f}" if v else ""),
                textposition="outside",
                offsetgroup="fill",
                hovertemplate=(
                    "%{x}<br>Лотов (вагонов): %{y:.0f}<br>"
                    "Тонн: %{customdata[0]:.0f}<br>Договоров: %{customdata[1]}"
                    "<extra></extra>"
                ),
                customdata=list(
                    zip(merged["Тонны залива"], merged["Договоров"])
                ),
            )
        )
        fig.add_hline(
            y=limit,
            line_dash="dash",
            line_color="#E67E22",
            annotation_text=f"Лимит заявок: {limit}",
            annotation_position="top left",
        )
        fig.update_layout(
            barmode="group",
            title="Заявки и залив базиса по инструментам",
            xaxis_title="Код инструмента",
            yaxis_title="Количество (заявки, шт / вагоны, лот)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            margin=dict(t=70, b=80, l=40, r=20),
            xaxis=dict(tickangle=-45),
        )
        return fig

    # dual_bars или bars_line — вторая ось Y для тонн
    fig.add_trace(
        go.Bar(
            name="Ваши заявки, шт",
            x=codes,
            y=merged["Количество"],
            marker_color=order_colors,
            text=merged["Количество"],
            textposition="outside",
            yaxis="y",
            hovertemplate="%{x}<br>Заявок: %{y}<extra></extra>",
        )
    )

    if variant == "bars_line":
        fig.add_trace(
            go.Scatter(
                name="Залив базиса, т",
                x=codes,
                y=merged["Тонны залива"],
                mode="lines+markers",
                line=dict(color="#2980B9", width=2),
                marker=dict(size=8),
                yaxis="y2",
                hovertemplate=(
                    "%{x}<br>Тонн: %{y:.0f}<br>"
                    "Вагонов: %{customdata[0]:.0f}<br>Договоров: %{customdata[1]}"
                    "<extra></extra>"
                ),
                customdata=list(zip(merged["Лоты"], merged["Договоров"])),
            )
        )
        title = "Заявки (столбики) и залив базиса, т (линия)"
    else:
        fig.add_trace(
            go.Bar(
                name="Залив базиса, т",
                x=codes,
                y=merged["Тонны залива"],
                marker_color="rgba(52, 152, 219, 0.75)",
                text=merged["Тонны залива"].map(lambda v: f"{v:.0f}" if v else ""),
                textposition="outside",
                yaxis="y2",
                hovertemplate=(
                    "%{x}<br>Тонн: %{y:.0f}<br>"
                    "Вагонов: %{customdata[0]:.0f}<br>Договоров: %{customdata[1]}"
                    "<extra></extra>"
                ),
                customdata=list(zip(merged["Лоты"], merged["Договоров"])),
            )
        )
        title = "Заявки и залив базиса по инструментам (две оси Y)"

    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color="#E67E22",
        annotation_text=f"Лимит заявок: {limit}",
        annotation_position="top left",
    )
    fig.update_layout(
        title=title,
        xaxis_title="Код инструмента",
        yaxis=dict(title="Количество заявок"),
        yaxis2=dict(
            title="Залив по договорам, т",
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0),
        margin=dict(t=80, b=80, l=40, r=60),
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
