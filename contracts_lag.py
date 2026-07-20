"""
Сопоставление журнала заявок с отчётом по договорам и оценка задержки реакции (мс).

Логика под сценарий трейдера: увидел сделку на рынке → отправил заявку.
Для каждой вашей заявки берётся последний договор по тому же инструменту,
который произошёл *до* фиксации заявки (в пределах окна по времени).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from utils import (
    format_timedelta,
    load_excel_raw,
    normalize_columns,
    parse_times_column,
    timedelta_to_seconds,
)

CONTRACT_REQUIRED = [
    "Время договора",
    "Код инструмента",
    "Цена",
    "Объем, лотов",
]

CONTRACT_ALIASES = {
    "Объем, лотов": ["Объём, лотов", "Объем лотов", "Объём лотов"],
    "Наименование и Базис поставки": ["Наименование инструмента"],
}


def _normalize_contract_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(df)
    rename_map = {}
    cols = list(df.columns)
    for canonical, aliases in CONTRACT_ALIASES.items():
        if canonical in cols:
            continue
        for alias in aliases:
            if alias in cols:
                rename_map[alias] = canonical
                break
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def validate_contract_columns(df: pd.DataFrame) -> Tuple[bool, str]:
    missing = [c for c in CONTRACT_REQUIRED if c not in df.columns]
    if missing:
        return False, (
            f"В отчёте по договорам нет колонок: {', '.join(missing)}. "
            f"Найдены: {', '.join(map(str, df.columns))}."
        )
    return True, ""


def prepare_contracts_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_contract_columns(df)
    ok, msg = validate_contract_columns(df)
    if not ok:
        raise ValueError(msg)

    result = df.copy()
    result["Время_td"] = parse_times_column(result["Время договора"])
    result["Время_сек"] = result["Время_td"].map(timedelta_to_seconds)

    for col in ("Цена", "Объем, лотов", "Объем, руб.", "Объем, нат. ед."):
        if col in result.columns:
            result[col] = pd.to_numeric(
                result[col]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace("\xa0", "", regex=False)
                .str.replace(" ", "", regex=False),
                errors="coerce",
            )

    if "Номер договора" in result.columns:
        result["Номер договора"] = result["Номер договора"].astype(str).str.strip()

    return result


def load_contracts_excel(uploaded_file) -> pd.DataFrame:
    raw = load_excel_raw(uploaded_file)
    if raw.empty:
        raise ValueError("Файл договоров не содержит данных.")
    return prepare_contracts_dataframe(raw)


def _contracts_by_instrument(df_contracts: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Договоры по инструменту, отсортированы по времени."""
    out: Dict[str, pd.DataFrame] = {}
    work = df_contracts.dropna(subset=["Время_сек", "Код инструмента"]).copy()
    for code, group in work.groupby("Код инструмента", sort=False):
        out[str(code)] = group.sort_values("Время_сек").reset_index(drop=True)
    return out


def match_orders_to_contracts(
    df_orders: pd.DataFrame,
    df_contracts: pd.DataFrame,
    *,
    max_lag_sec: float = 120.0,
    only_buys: bool = True,
    prefer_executed: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Для каждой вашей заявки находит последний рыночный договор по тому же инструменту
  до момента фиксации заявки.

    Задержка реакции, мс = время заявки − время договора (всегда ≥ 0 для найденных пар).
    Цена и объём договора с заявкой могут не совпадать — так и бывает при реакции на сделку.
    """
    del prefer_executed  # оставлен в сигнатуре для совместимости с app.py

    meta: Dict[str, Any] = {
        "contracts_total": len(df_contracts),
        "orders_total": 0,
        "matched": 0,
        "unmatched_orders": 0,
        "orders_before_first_contract": 0,
    }

    if df_contracts.empty or df_orders.empty:
        return pd.DataFrame(), meta

    orders = df_orders.copy()
    if only_buys and "_is_buy" in orders.columns:
        orders = orders[orders["_is_buy"]].copy()

    orders = orders.dropna(subset=["Время_сек", "Код инструмента"]).copy()
    meta["orders_total"] = len(orders)
    if orders.empty:
        return pd.DataFrame(), meta

    by_inst = _contracts_by_instrument(df_contracts)
    if not by_inst:
        return pd.DataFrame(), meta

    rows: List[dict] = []
    orders_sorted = orders.sort_values("Время_сек")

    for _, o in orders_sorted.iterrows():
        o_time = float(o["Время_сек"])
        code = str(o["Код инструмента"])
        inst_contracts = by_inst.get(code)
        if inst_contracts is None or inst_contracts.empty:
            meta["unmatched_orders"] += 1
            continue

        times = inst_contracts["Время_сек"].astype(float)
        # договоры строго до заявки
        mask_before = times < o_time
        if not mask_before.any():
            meta["orders_before_first_contract"] += 1
            meta["unmatched_orders"] += 1
            continue

        before = inst_contracts.loc[mask_before]
        lag_sec = o_time - before["Время_сек"].astype(float)
        within = lag_sec <= max_lag_sec
        if not within.any():
            meta["unmatched_orders"] += 1
            continue

        # последний договор перед заявкой в пределах окна
        idx = before.loc[within, "Время_сек"].astype(float).idxmax()
        c = inst_contracts.loc[idx]
        reaction_ms = (o_time - float(c["Время_сек"])) * 1000
        rows.append(_reaction_row(c, o, reaction_ms))
        meta["matched"] += 1

    result = pd.DataFrame(rows)
    return result, meta


def _reaction_row(c, o, reaction_ms: float) -> dict:
    return {
        "Номер договора": c.get("Номер договора", ""),
        "Время договора": format_timedelta(c.get("Время_td")),
        "Цена договора": c.get("Цена"),
        "Лоты договора": c.get("Объем, лотов"),
        "Номер заявки": o.get("Номер заявки", ""),
        "Время заявки": format_timedelta(o.get("Время_td")),
        "Цена заявки": o.get("Цена"),
        "Лоты заявки": o.get("Объем, лотов"),
        "Код инструмента": c.get("Код инструмента"),
        "Статус заявки": o.get("Статус", ""),
        "Задержка реакции, мс": round(reaction_ms, 1),
        "Тип сопоставления": "заявка после договора",
    }


def lag_summary(matched: pd.DataFrame) -> Dict[str, Any]:
    """Сводка по задержке реакции (мс)."""
    col = "Задержка реакции, мс"
    if matched.empty or col not in matched.columns:
        return {"after_count": 0}

    delays = matched[col].dropna()
    if delays.empty:
        return {"after_count": 0}

    return {
        "after_count": int(len(delays)),
        "after_min_ms": round(float(delays.min()), 1),
        "after_median_ms": round(float(delays.median()), 1),
        "after_p90_ms": round(float(delays.quantile(0.9)), 1),
        "after_max_ms": round(float(delays.max()), 1),
        "after_mean_ms": round(float(delays.mean()), 1),
    }


def fig_lag_histogram(matched: pd.DataFrame):
    import plotly.express as px

    col = "Задержка реакции, мс"
    if matched.empty or col not in matched.columns:
        from analytics import _empty_fig

        return _empty_fig("Нет сопоставленных пар заявка–договор")

    work = matched.sort_values(col)
    fig = px.scatter(
        work,
        x="Время заявки",
        y=col,
        hover_data=[
            "Время договора",
            "Номер заявки",
            "Номер договора",
            "Код инструмента",
            "Цена договора",
            "Цена заявки",
        ],
        title="Задержка от последней сделки до регистрации вашей заявки",
        labels={
            "Время заявки": "Время вашей заявки",
            col: "Задержка реакции, мс",
        },
    )
    fig.update_traces(marker=dict(size=9, color="#c0392b"))
    fig.update_layout(margin=dict(t=50, b=40, l=40, r=20))
    return fig
