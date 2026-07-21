"""
Графики «залив базиса» на вкладке лимитов.
Отдельный модуль — стабильный импорт на Streamlit Cloud (см. rhythm_guide, help_texts).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from analytics import fig_instruments_limit, instruments_order_counts


def _empty_fig(message: str) -> go.Figure:
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

    merged["Количество"] = (
        pd.to_numeric(merged.get("Количество"), errors="coerce").fillna(0).astype(int)
    )
    merged["Договоров"] = (
        pd.to_numeric(merged.get("Договоров"), errors="coerce").fillna(0).astype(int)
    )
    merged["Лоты"] = pd.to_numeric(merged.get("Лоты"), errors="coerce").fillna(0.0)
    merged["Тонны залива"] = (
        pd.to_numeric(merged.get("Тонны залива"), errors="coerce").fillna(0.0)
    )
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
                customdata=list(zip(merged["Тонны залива"], merged["Договоров"])),
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
