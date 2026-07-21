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
    normalize_instrument_code,
    parse_times_column,
    pick_first_nonempty,
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
    if "Код инструмента" in result.columns:
        result["Код инструмента"] = result["Код инструмента"].map(
            normalize_instrument_code
        )
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
        out[normalize_instrument_code(code)] = group.sort_values(
            "Время_сек"
        ).reset_index(drop=True)
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
        code = normalize_instrument_code(o["Код инструмента"])
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
        from trade_analytics import _empty_fig

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


def wagon_tons_per_lot(code: str, name: str = "") -> float:
    """
    Тонн в одном лоте (вагоне): бензин — 60, дизель — 65.
    По коду и наименованию инструмента; иначе 60.
    """
    blob = f"{code} {name}".lower().replace("ё", "е")
    diesel_keys = ("диз", "дт", "diesel", "дизел")
    if any(k in blob for k in diesel_keys):
        return 65.0
    return 60.0


DEFAULT_PROLIV_SPREAD_MS = 5.0


def count_proliv_clusters(times_sec: pd.Series, spread_ms: float = DEFAULT_PROLIV_SPREAD_MS) -> int:
    """
    Число «проливов»: группы договоров по одному инструменту,
    где соседние по времени отличаются не более чем на spread_ms (обычно 3–5 мс).
    """
    t = pd.to_numeric(times_sec, errors="coerce").dropna().sort_values()
    if t.empty:
        return 0
    spread_sec = float(spread_ms) / 1000.0
    clusters = 1
    last = float(t.iloc[0])
    for val in t.iloc[1:]:
        v = float(val)
        if v - last > spread_sec:
            clusters += 1
        last = v
    return int(clusters)


def assign_proliv_ids(times_sec: pd.Series, spread_ms: float = DEFAULT_PROLIV_SPREAD_MS) -> pd.Series:
    """Номер пролива (1, 2, …) для каждой строки в том же порядке, что и times_sec.index."""
    spread_sec = float(spread_ms) / 1000.0
    out = pd.Series(index=times_sec.index, dtype="Int64")
    valid = pd.to_numeric(times_sec, errors="coerce")
    order = valid.dropna().sort_values().index
    if len(order) == 0:
        return out

    proliv = 1
    last = float(valid.loc[order[0]])
    out.loc[order[0]] = proliv
    for idx in order[1:]:
        v = float(valid.loc[idx])
        if v - last > spread_sec:
            proliv += 1
        out.loc[idx] = proliv
        last = v
    return out


def aggregate_basis_fill_by_instrument(
    df_contracts: pd.DataFrame,
    *,
    proliv_spread_ms: float = DEFAULT_PROLIV_SPREAD_MS,
) -> pd.DataFrame:
    """
    Суммарный залив по инструменту: все договоры в переданном фрейме
    (уже отфильтрованном по сессии при необходимости).
    """
    empty_cols = [
        "Код инструмента",
        "Наименование_договора",
        "Договоров",
        "Проливов",
        "Лоты",
        "Тонны залива",
    ]
    if df_contracts is None or df_contracts.empty:
        return pd.DataFrame(columns=empty_cols)

    work = df_contracts.dropna(subset=["Код инструмента"]).copy()
    if work.empty:
        return pd.DataFrame(columns=empty_cols)

    name_col = (
        "Наименование и Базис поставки"
        if "Наименование и Базис поставки" in work.columns
        else None
    )
    has_lots = "Объем, лотов" in work.columns

    rows: List[dict] = []
    for code, group in work.groupby("Код инструмента", sort=False):
        code_s = normalize_instrument_code(code)
        if name_col:
            name = pick_first_nonempty(group[name_col])
        else:
            name = ""
        lots = (
            float(group["Объем, лотов"].fillna(0).sum())
            if has_lots
            else 0.0
        )
        t_per = wagon_tons_per_lot(code_s, name)
        times = (
            group["Время_сек"]
            if "Время_сек" in group.columns
            else pd.Series(dtype=float)
        )
        if times is None:
            times = pd.Series(dtype=float)
        prolivs = count_proliv_clusters(times, spread_ms=proliv_spread_ms)
        rows.append(
            {
                "Код инструмента": code_s,
                "Наименование_договора": name,
                "Договоров": int(len(group)),
                "Проливов": prolivs,
                "Лоты": lots,
                "Тонны залива": lots * t_per,
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=empty_cols)
    return result.sort_values("Тонны залива", ascending=False).reset_index(drop=True)


def contracts_detail_for_instrument(
    df_contracts: pd.DataFrame,
    instrument_code: str,
    *,
    proliv_spread_ms: float = DEFAULT_PROLIV_SPREAD_MS,
) -> pd.DataFrame:
    """Договоры по одному инструменту для детализации залива."""
    cols_out = [
        "Пролив",
        "Номер договора",
        "Время договора",
        "Объем, лотов",
        "Тонн",
        "Цена",
    ]
    if df_contracts is None or df_contracts.empty:
        return pd.DataFrame(columns=cols_out)

    work = df_contracts[
        df_contracts["Код инструмента"].map(normalize_instrument_code)
        == normalize_instrument_code(instrument_code)
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=cols_out)

    name = ""
    if "Наименование и Базис поставки" in work.columns:
        s = work["Наименование и Базис поставки"].dropna()
        if len(s):
            name = str(s.iloc[0])
    t_per = wagon_tons_per_lot(str(instrument_code), name)

    if "Время_сек" in work.columns:
        work = work.sort_values("Время_сек", na_position="last")
    if "Время_td" in work.columns:
        work["Время договора"] = work["Время_td"].map(format_timedelta)
    elif "Время договора" not in work.columns:
        work["Время договора"] = ""

    lots = work["Объем, лотов"] if "Объем, лотов" in work.columns else 0
    work["Тонн"] = pd.to_numeric(lots, errors="coerce").fillna(0) * t_per

    out = work.copy()
    if "Номер договора" not in out.columns:
        out["Номер договора"] = ""
    if "Цена" not in out.columns:
        out["Цена"] = None
    if "Объем, лотов" not in out.columns:
        out["Объем, лотов"] = 0

    if "Время_сек" in out.columns:
        out["Пролив"] = assign_proliv_ids(out["Время_сек"], spread_ms=proliv_spread_ms)
    else:
        out["Пролив"] = 1

    return out[cols_out].reset_index(drop=True)
