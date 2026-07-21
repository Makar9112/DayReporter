"""
Графики «залив базиса» на вкладке лимитов.
Отдельный модуль — стабильный импорт на Streamlit Cloud (см. rhythm_guide, help_texts).
"""

from __future__ import annotations

from typing import Literal, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trade_analytics import fig_instruments_limit, instruments_order_counts

ScopeMode = Literal["my", "all"]
RankBy = Literal["activity", "orders", "fill_tons"]
ChartVariant = Literal["split_panels", "horizontal", "dual_bars", "bars_line", "grouped"]


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


def _attach_fill_names(merged: pd.DataFrame, fill_by_inst: pd.DataFrame) -> pd.DataFrame:
    """Наименование из журнала; из договора — только если в журнале пусто."""
    if "Наименование" not in merged.columns:
        merged["Наименование"] = ""
    if "Наименование_договора" not in fill_by_inst.columns:
        return merged

    name_fill = fill_by_inst[["Код инструмента", "Наименование_договора"]].copy()
    name_fill["Код инструмента"] = name_fill["Код инструмента"].astype(str)
    merged = merged.merge(
        name_fill.rename(columns={"Наименование_договора": "_name_contract"}),
        on="Код инструмента",
        how="left",
    )

    def _resolve(row) -> str:
        journal = str(row.get("Наименование") or "").strip()
        if journal and journal.lower() != "nan":
            return journal
        contract = str(row.get("_name_contract") or "").strip()
        if contract and contract.lower() != "nan":
            return contract
        return ""

    merged["Наименование"] = merged.apply(_resolve, axis=1)
    merged.drop(columns=["_name_contract"], errors="ignore", inplace=True)
    return merged


def _enrich_labels(merged: pd.DataFrame) -> pd.DataFrame:
    merged = merged.copy()
    merged["Наименование"] = merged["Наименование"].fillna("").astype(str).str.strip()
    return merged


def _y_labels_with_names(df: pd.DataFrame) -> pd.Series:
    """Подпись оси: код и укороченное наименование из журнала."""
    out = []
    for _, row in df.iterrows():
        code = str(row["Код инструмента"])
        name = str(row.get("Наименование") or "").strip()
        if not name:
            out.append(code)
            continue
        short = name if len(name) <= 42 else name[:41] + "…"
        out.append(f"{code} · {short}")
    return pd.Series(out, index=df.index)


def merge_orders_and_basis_fill(
    df: pd.DataFrame,
    fill_by_inst: Optional[pd.DataFrame],
    *,
    scope: ScopeMode = "my",
) -> pd.DataFrame:
    """
    Объединяет заявки и залив по инструменту.

    scope=my — только коды из вашего журнала (рекомендуется для графика).
    scope=all — все инструменты из файла договоров (outer join).
    """
    counts = instruments_order_counts(df)
    if fill_by_inst is None or fill_by_inst.empty:
        out = counts.copy()
        out["Договоров"] = 0
        out["Лоты"] = 0.0
        out["Тонны залива"] = 0.0
        return _finalize_merged(out)

    fill = fill_by_inst[
        ["Код инструмента", "Договоров", "Лоты", "Тонны залива"]
    ].copy()
    fill["Код инструмента"] = fill["Код инструмента"].astype(str)

    if counts.empty and scope == "my":
        return pd.DataFrame(
            columns=[
                "Код инструмента",
                "Наименование",
                "Количество",
                "Договоров",
                "Лоты",
                "Тонны залива",
            ]
        )

    if counts.empty:
        merged = fill.copy()
        merged["Количество"] = 0
        merged["Наименование"] = ""
        merged = _attach_fill_names(merged, fill_by_inst)
    else:
        counts = counts.copy()
        counts["Код инструмента"] = counts["Код инструмента"].astype(str)
        how: Literal["left", "outer"] = "left" if scope == "my" else "outer"
        merged = counts.merge(fill, on="Код инструмента", how=how)
        merged = _attach_fill_names(merged, fill_by_inst)

    return _finalize_merged(merged)


def _finalize_merged(merged: pd.DataFrame) -> pd.DataFrame:
    if merged.empty:
        return merged
    merged = merged.copy()
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
    return merged


def apply_top_n(
    merged: pd.DataFrame,
    top_n: int,
    rank_by: RankBy = "activity",
) -> pd.DataFrame:
    """Оставляет top_n строк после сортировки (0 — без ограничения)."""
    if merged.empty or top_n <= 0 or len(merged) <= top_n:
        return merged.sort_values(
            ["Количество", "Тонны залива"], ascending=[False, False]
        ).reset_index(drop=True)

    work = merged.copy()
    if rank_by == "orders":
        work["_sort"] = work["Количество"]
    elif rank_by == "fill_tons":
        work["_sort"] = work["Тонны залива"]
    else:
        # Сводный «интерес»: заявки + эквивалент ~300 т ≈ 1 ед. для сортировки
        work["_sort"] = work["Количество"] + work["Тонны залива"] / 300.0

    work = work.sort_values("_sort", ascending=False).head(top_n)
    work = work.drop(columns=["_sort"], errors="ignore")
    return work.sort_values(
        ["Количество", "Тонны залива"], ascending=[False, False]
    ).reset_index(drop=True)


def prepare_limits_chart_frame(
    df: pd.DataFrame,
    fill_by_inst: Optional[pd.DataFrame],
    *,
    scope: ScopeMode = "my",
    top_n: int = 20,
    rank_by: RankBy = "activity",
) -> pd.DataFrame:
    merged = merge_orders_and_basis_fill(df, fill_by_inst, scope=scope)
    merged = apply_top_n(merged, top_n, rank_by=rank_by)
    return _enrich_labels(merged)


def _chart_height(n: int, *, per_row: int = 28, base: int = 120) -> int:
    return min(max(base + n * per_row, 280), 900)


def fig_instruments_limit_with_basis_fill(
    df: pd.DataFrame,
    fill_by_inst: Optional[pd.DataFrame],
    limit: int = 250,
    *,
    variant: ChartVariant = "split_panels",
    scope: ScopeMode = "my",
    top_n: int = 20,
    rank_by: RankBy = "activity",
) -> go.Figure:
    """
    Лимиты заявок + залив базиса.

    variant: split_panels | horizontal | dual_bars | bars_line | grouped
    """
    merged = prepare_limits_chart_frame(
        df,
        fill_by_inst,
        scope=scope,
        top_n=top_n,
        rank_by=rank_by,
    )
    if merged.empty:
        return _empty_fig("Нет данных по инструментам")

    has_fill = (
        fill_by_inst is not None
        and not fill_by_inst.empty
        and float(merged["Тонны залива"].sum()) > 0
    )

    if not has_fill:
        if scope == "my" and top_n > 0 and len(merged) < len(instruments_order_counts(df)):
            codes = set(merged["Код инструмента"].astype(str))
            sub = df[df["Код инструмента"].astype(str).isin(codes)]
            return fig_instruments_limit(sub, limit=limit)
        return fig_instruments_limit(df, limit=limit)

    merged = merged.copy()
    merged["Превышение"] = merged["Количество"] > limit
    order_colors = merged["Превышение"].map({True: "#C0392B", False: "#27AE60"})
    codes = merged["Код инструмента"].astype(str)
    n = len(merged)
    scope_note = "ваши инструменты" if scope == "my" else "все из договоров"
    top_note = f", топ {n}" if top_n > 0 and n <= top_n else f", {n} шт."

    if variant == "split_panels":
        return _fig_split_panels(
            merged, codes, order_colors, limit, scope_note, top_note, n
        )
    if variant == "horizontal":
        return _fig_horizontal_dual(
            merged, codes, order_colors, limit, scope_note, top_note, n
        )
    if variant == "grouped":
        return _fig_grouped(merged, codes, order_colors, limit, scope_note, top_note, n)

    return _fig_dual_axis(
        merged,
        codes,
        order_colors,
        limit,
        variant,
        scope_note,
        top_note,
        n,
    )


def _fig_split_panels(
    merged, codes, order_colors, limit, scope_note, top_note, n
) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=("Ваши заявки, шт", "Залив базиса, т"),
    )
    fig.add_trace(
        go.Bar(
            x=codes,
            y=merged["Количество"],
            marker_color=order_colors,
            text=merged["Количество"],
            textposition="outside",
            name="Заявки",
            customdata=merged["Наименование"].replace("", "—"),
            hovertemplate="%{x}<br>%{customdata}<br>Заявок: %{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color="#E67E22",
        annotation_text=f"Лимит {limit}",
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=codes,
            y=merged["Тонны залива"],
            marker_color="#3498DB",
            text=merged["Тонны залива"].map(lambda v: f"{v:.0f}" if v else ""),
            textposition="outside",
            name="Залив, т",
            hovertemplate=(
                "%{x}<br>%{customdata[2]}<br>Тонн: %{y:.0f}<br>"
                "Вагонов: %{customdata[0]:.0f}<br>Договоров: %{customdata[1]}"
                "<extra></extra>"
            ),
            customdata=list(
                zip(
                    merged["Лоты"],
                    merged["Договоров"],
                    merged["Наименование"].replace("", "—"),
                )
            ),
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        title=f"Заявки и залив ({scope_note}{top_note})",
        height=_chart_height(n, per_row=22, base=200),
        showlegend=False,
        margin=dict(t=80, b=60, l=50, r=30),
    )
    fig.update_xaxes(tickangle=-45, row=2, col=1)
    fig.update_xaxes(title_text="Код инструмента", row=2, col=1)
    fig.update_yaxes(title_text="Заявки", row=1, col=1)
    fig.update_yaxes(title_text="Тонн", row=2, col=1)
    return fig


def _fig_horizontal_dual(
    merged, codes, order_colors, limit, scope_note, top_note, n
) -> go.Figure:
    codes_list = list(reversed(codes.tolist()))
    m = merged.copy()
    m["Код инструмента"] = m["Код инструмента"].astype(str)
    m = m.set_index("Код инструмента").loc[codes_list].reset_index()
    colors = m["Количество"].gt(limit).map({True: "#C0392B", False: "#27AE60"})
    y_labels = _y_labels_with_names(m)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            orientation="h",
            y=y_labels,
            x=m["Количество"],
            name="Ваши заявки, шт",
            marker_color=colors,
            text=m["Количество"],
            textposition="outside",
            customdata=m["Наименование"].replace("", "—"),
            hovertemplate="%{customdata}<br>Заявок: %{x}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            orientation="h",
            y=y_labels,
            x=m["Тонны залива"],
            name="Залив базиса, т",
            marker_color="rgba(52, 152, 219, 0.55)",
            xaxis="x2",
            text=m["Тонны залива"].map(lambda v: f"{v:.0f}" if v else ""),
            textposition="outside",
            hovertemplate=(
                "%{customdata[2]}<br>Тонн: %{x:.0f}<br>"
                "Вагонов: %{customdata[0]:.0f}<br>Договоров: %{customdata[1]}"
                "<extra></extra>"
            ),
            customdata=list(
                zip(
                    m["Лоты"],
                    m["Договоров"],
                    m["Наименование"].replace("", "—"),
                )
            ),
        )
    )
    fig.add_vline(
        x=limit,
        line_dash="dash",
        line_color="#E67E22",
        annotation_text=f"Лимит {limit}",
    )
    fig.update_layout(
        title=f"Заявки и залив, горизонтально ({scope_note}{top_note})",
        barmode="overlay",
        height=_chart_height(n, per_row=32, base=100),
        xaxis=dict(title="Количество заявок"),
        xaxis2=dict(
            title="Залив, т",
            overlaying="x",
            side="top",
            showgrid=False,
        ),
        yaxis=dict(title="", automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=70, b=40, l=120, r=40),
    )
    return fig


def _fig_grouped(
    merged, codes, order_colors, limit, scope_note, top_note, n
) -> go.Figure:
    fig = go.Figure()
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
                "%{x}<br>Лотов: %{y:.0f}<br>Тонн: %{customdata[0]:.0f}<extra></extra>"
            ),
            customdata=list(merged["Тонны залива"]),
        )
    )
    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color="#E67E22",
        annotation_text=f"Лимит заявок: {limit}",
    )
    fig.update_layout(
        barmode="group",
        title=f"Заявки и вагоны ({scope_note}{top_note})",
        xaxis_title="Код инструмента",
        yaxis_title="Шт / лот",
        height=_chart_height(n, per_row=24, base=140),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=70, b=80, l=40, r=20),
        xaxis=dict(tickangle=-45),
    )
    return fig


def _fig_dual_axis(
    merged, codes, order_colors, limit, variant, scope_note, top_note, n
) -> go.Figure:
    fig = go.Figure()
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
                hovertemplate="%{x}<br>Тонн: %{y:.0f}<extra></extra>",
            )
        )
        title = f"Заявки + залив линией ({scope_note}{top_note})"
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
                hovertemplate="%{x}<br>Тонн: %{y:.0f}<extra></extra>",
            )
        )
        title = f"Две оси Y ({scope_note}{top_note})"

    fig.add_hline(y=limit, line_dash="dash", line_color="#E67E22")
    fig.update_layout(
        title=title,
        height=_chart_height(n, per_row=24, base=140),
        xaxis_title="Код инструмента",
        yaxis=dict(title="Количество заявок"),
        yaxis2=dict(
            title="Залив, т",
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0),
        margin=dict(t=80, b=80, l=40, r=60),
        xaxis=dict(tickangle=-45),
    )
    return fig
