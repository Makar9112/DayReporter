"""
Сопоставление журнала заявок с отчётом по договорам и оценка задержки (мс).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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


def _price_match(a, b, eps: float = 0.01) -> bool:
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= eps


def _lots_match(a, b) -> bool:
    if pd.isna(a) or pd.isna(b):
        return False
    return int(round(float(a))) == int(round(float(b)))


def match_orders_to_contracts(
    df_orders: pd.DataFrame,
    df_contracts: pd.DataFrame,
    *,
    max_lag_sec: float = 120.0,
    only_buys: bool = True,
    prefer_executed: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Для каждого договора ищет вашу заявку с тем же инструментом, ценой и лотами.

    Возвращает ближайшую пару:
    - если заявка была раньше договора, `Разница, мс` будет положительной, а тип "заявка раньше договора";
    - если заявка была позже договора, `Разница, мс` будет отрицательной, а тип "заявка после договора".
    """
    meta: Dict[str, Any] = {
        "contracts_total": len(df_contracts),
        "orders_total": len(df_orders),
        "matched": 0,
        "unmatched_contracts": 0,
        "late_orders": 0,
    }

    if df_contracts.empty or df_orders.empty:
        return pd.DataFrame(), meta

    orders = df_orders.copy()
    if only_buys and "_is_buy" in orders.columns:
        orders = orders[orders["_is_buy"]].copy()

    contracts = df_contracts.dropna(subset=["Время_сек"]).copy()
    orders = orders.dropna(subset=["Время_сек"]).copy()

    if contracts.empty or orders.empty:
        return pd.DataFrame(), meta

    if prefer_executed and "_is_executed" in orders.columns:
        orders_sorted = pd.concat(
            [
                orders[orders["_is_executed"]],
                orders[~orders["_is_executed"]],
            ],
            ignore_index=True,
        )
    else:
        orders_sorted = orders

    rows: List[dict] = []
    used_order_idx: set = set()

    for _, c in contracts.iterrows():
        c_time = c["Время_сек"]
        code = c.get("Код инструмента")
        price = c.get("Цена")
        lots = c.get("Объем, лотов")

        candidates = orders_sorted[
            (orders_sorted["Код инструмента"] == code)
            & orders_sorted["Цена"].apply(lambda p: _price_match(p, price))
            & orders_sorted["Объем, лотов"].apply(lambda v: _lots_match(v, lots))
        ]

        if candidates.empty:
            meta["unmatched_contracts"] += 1
            continue

        best_idx = None
        best_abs_diff = None
        best_diff_sec = None
        for idx, o in candidates.iterrows():
            if idx in used_order_idx:
                continue
            diff_sec = c_time - o["Время_сек"]
            if abs(diff_sec) > max_lag_sec:
                continue
            if best_abs_diff is None or abs(diff_sec) < best_abs_diff:
                best_abs_diff = abs(diff_sec)
                best_diff_sec = diff_sec
                best_idx = idx

        if best_idx is None:
            meta["unmatched_contracts"] += 1
            continue

        o = orders_sorted.loc[best_idx]
        used_order_idx.add(best_idx)
        diff_ms = best_diff_sec * 1000
        if diff_ms < 0:
            meta["late_orders"] += 1
            match_type = "заявка после договора"
        else:
            match_type = "заявка раньше договора"
        rows.append(_match_row(c, o, diff_ms, match_type=match_type))
        meta["matched"] += 1

    result = pd.DataFrame(rows)
    if not result.empty:
        result["Задержка после договора, мс"] = result["Разница, мс"].map(
            lambda v: round(abs(v), 1) if v < 0 else None
        )
        result["Фора до договора, мс"] = result["Разница, мс"].map(
            lambda v: round(v, 1) if v > 0 else None
        )
    return result, meta


def _match_row(c, o, diff_ms: float, match_type: str) -> dict:
    return {
        "Номер договора": c.get("Номер договора", ""),
        "Время договора": format_timedelta(c.get("Время_td")),
        "Номер заявки": o.get("Номер заявки", ""),
        "Время заявки": format_timedelta(o.get("Время_td")),
        "Код инструмента": c.get("Код инструмента"),
        "Цена": c.get("Цена"),
        "Объем, лотов": c.get("Объем, лотов"),
        "Статус заявки": o.get("Статус", ""),
        "Разница, мс": round(diff_ms, 1),
        "Тип сопоставления": match_type,
    }


def lag_summary(matched: pd.DataFrame) -> Dict[str, Any]:
    """Сводка по двум сценариям: заявка позже сделки и заявка раньше сделки."""
    if matched.empty or "Разница, мс" not in matched.columns:
        return {}

    after = matched[matched["Разница, мс"] < 0]["Разница, мс"].abs()
    before = matched[matched["Разница, мс"] > 0]["Разница, мс"]

    summary: Dict[str, Any] = {
        "after_count": int(len(after)),
        "before_count": int(len(before)),
    }

    if len(after):
        summary.update(
            {
                "after_min_ms": round(float(after.min()), 1),
                "after_median_ms": round(float(after.median()), 1),
                "after_p90_ms": round(float(after.quantile(0.9)), 1),
                "after_max_ms": round(float(after.max()), 1),
                "after_mean_ms": round(float(after.mean()), 1),
            }
        )

    if len(before):
        summary.update(
            {
                "before_min_ms": round(float(before.min()), 1),
                "before_median_ms": round(float(before.median()), 1),
                "before_p90_ms": round(float(before.quantile(0.9)), 1),
                "before_max_ms": round(float(before.max()), 1),
                "before_mean_ms": round(float(before.mean()), 1),
            }
        )

    return summary


def fig_lag_histogram(matched: pd.DataFrame):
    import plotly.express as px

    if matched.empty:
        from analytics import _empty_fig

        return _empty_fig("Нет сопоставленных пар заявка–договор")

    after = matched[matched["Разница, мс"] < 0].copy()
    if after.empty:
        from analytics import _empty_fig

        return _empty_fig("Нет случаев, где заявка зарегистрирована после договора")

    after["Задержка после договора, мс"] = after["Разница, мс"].abs()
    after["Подпись"] = (
        after["Время договора"].astype(str)
        + " -> "
        + after["Время заявки"].astype(str)
    )

    fig = px.scatter(
        after.sort_values("Задержка после договора, мс"),
        x="Время договора",
        y="Задержка после договора, мс",
        hover_data=[
            "Время заявки",
            "Номер заявки",
            "Номер договора",
            "Код инструмента",
            "Цена",
            "Объем, лотов",
        ],
        title="Сколько прошло от сделки до регистрации вашей заявки",
        labels={
            "Время договора": "Время сделки",
            "Задержка после договора, мс": "Задержка после сделки, мс",
        },
    )
    fig.update_traces(marker=dict(size=9, color="#c0392b"))
    fig.update_layout(margin=dict(t=50, b=40, l=40, r=20))
    return fig
