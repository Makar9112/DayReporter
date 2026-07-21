"""
Загрузка справочника лимитов по кодам инструмента (Security code, Max orders и др.).
Формат — .properties / таблица из настроек терминала (без привязки к журналу заявок).
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from utils import normalize_instrument_code

_CANONICAL_COLS = [
    "Код инструмента",
    "Max orders",
    "Лот на заявку",
    "Макс. лотов",
]


def _empty_limits() -> pd.DataFrame:
    return pd.DataFrame(columns=_CANONICAL_COLS)


def _read_text(uploaded) -> str:
    if hasattr(uploaded, "getvalue"):
        raw = uploaded.getvalue()
        if hasattr(uploaded, "seek"):
            try:
                uploaded.seek(0)
            except Exception:
                pass
    elif hasattr(uploaded, "read"):
        raw = uploaded.read()
    else:
        raw = uploaded
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _header_map(header: List[str]) -> Dict[str, int]:
    norm = {i: h.strip().lower().replace(" ", "") for i, h in enumerate(header)}
    idx: Dict[str, int] = {}

    def find(*needles: str) -> Optional[int]:
        for i, h in norm.items():
            if all(n in h for n in needles):
                return i
        return None

    code_i = find("security", "code") or find("code")
    if code_i is None:
        for i, h in norm.items():
            if h in ("code", "securitycode", "кодинструмента"):
                code_i = i
                break
    en_i = find("enabled") or (0 if norm.get(0) == "enabled" else None)
    mo = find("max", "order") or find("maxorders")
    os_i = find("order", "size") or find("ordersize")
    mq = find("max", "quant") or find("maxquantity")
    if code_i is not None:
        idx["code"] = code_i
    if en_i is not None:
        idx["enabled"] = en_i
    if mo is not None:
        idx["max_orders"] = mo
    if os_i is not None:
        idx["order_size"] = os_i
    if mq is not None:
        idx["max_quantity"] = mq
    return idx


def _parse_delimited_table(text: str) -> pd.DataFrame:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) < 2:
        return _empty_limits()

    sep = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else ",")
    rows: List[List[str]] = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(sep)]
        rows.append(parts)

    header = rows[0]
    idx = _header_map(header)
    if "code" not in idx:
        return _empty_limits()

    out_rows: List[dict] = []
    for parts in rows[1:]:
        if len(parts) <= idx["code"]:
            continue
        code = normalize_instrument_code(parts[idx["code"]])
        if not code:
            continue
        if idx.get("enabled") is not None and len(parts) > idx["enabled"]:
            en = parts[idx["enabled"]].strip().lower()
            if en in ("0", "false", "no", "нет"):
                continue
        rec: Dict[str, Any] = {"Код инструмента": code}
        if "max_orders" in idx and len(parts) > idx["max_orders"]:
            rec["Max orders"] = _to_int(parts[idx["max_orders"]])
        if "order_size" in idx and len(parts) > idx["order_size"]:
            rec["Лот на заявку"] = _to_int(parts[idx["order_size"]])
        if "max_quantity" in idx and len(parts) > idx["max_quantity"]:
            rec["Макс. лотов"] = _to_int(parts[idx["max_quantity"]])
        out_rows.append(rec)

    if not out_rows:
        return _empty_limits()
    return _finalize_limits_df(pd.DataFrame(out_rows))


def _to_int(val) -> Optional[int]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace(",", ".")
    if not s or s.lower() == "nan":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_properties(text: str) -> pd.DataFrame:
    """
    Ключи вида:
    - securities[0].securityCode=A692... / maxOrders=10
    - security.A692....maxOrders=10
    - 0.code=A692...
    """
    by_index: Dict[str, Dict[str, Any]] = {}
    by_code: Dict[str, Dict[str, Any]] = {}

    key_re = re.compile(
        r"^(?:securities\[(\d+)\]|(?:security\.)?([A-Za-z0-9]+)|(\d+))\.?"
        r"(securityCode|security_code|code|orderSize|order_size|"
        r"maxQuantity|max_quantity|maxOrders|max_orders|enabled)\s*=\s*(.+)$",
        re.IGNORECASE,
    )
    flat_re = re.compile(
        r"^(security\.)?([A-Za-z0-9]{4,})\.(orderSize|maxQuantity|maxOrders|enabled)\s*=\s*(.+)$",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        m = key_re.match(line)
        if m:
            idx, code_inline, num_idx, field, val = m.groups()
            field_l = field.lower()
            val = val.strip()
            bucket_key = idx or num_idx or code_inline
            if bucket_key is None:
                continue
            bucket = by_index.setdefault(str(bucket_key), {})
            if field_l in ("securitycode", "security_code", "code"):
                bucket["code"] = normalize_instrument_code(val)
            elif field_l in ("ordersize", "order_size"):
                bucket["order_size"] = _to_int(val)
            elif field_l in ("maxquantity", "max_quantity"):
                bucket["max_quantity"] = _to_int(val)
            elif field_l in ("maxorders", "max_orders"):
                bucket["max_orders"] = _to_int(val)
            elif field_l == "enabled":
                bucket["enabled"] = val.lower() not in ("0", "false", "no")
            continue

        m2 = flat_re.match(line)
        if m2:
            _, code, field, val = m2.groups()
            code_s = normalize_instrument_code(code)
            if not code_s:
                continue
            bucket = by_code.setdefault(code_s, {"code": code_s})
            field_l = field.lower()
            if field_l == "ordersize":
                bucket["order_size"] = _to_int(val)
            elif field_l == "maxquantity":
                bucket["max_quantity"] = _to_int(val)
            elif field_l == "maxorders":
                bucket["max_orders"] = _to_int(val)
            elif field_l == "enabled":
                bucket["enabled"] = val.lower() not in ("0", "false", "no")

    rows: List[dict] = []
    for bucket in list(by_index.values()) + list(by_code.values()):
        code = bucket.get("code")
        if not code:
            continue
        if bucket.get("enabled") is False:
            continue
        rows.append(
            {
                "Код инструмента": normalize_instrument_code(code),
                "Max orders": bucket.get("max_orders"),
                "Лот на заявку": bucket.get("order_size"),
                "Макс. лотов": bucket.get("max_quantity"),
            }
        )

    if not rows:
        return _empty_limits()
    return _finalize_limits_df(pd.DataFrame(rows))


def _finalize_limits_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Код инструмента"] = df["Код инструмента"].map(normalize_instrument_code)
    df = df[df["Код инструмента"].astype(str).str.len() > 0]
    for col in ("Max orders", "Лот на заявку", "Макс. лотов"):
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = df[col].map(_to_int)
    df = df.drop_duplicates(subset=["Код инструмента"], keep="last")
    return df[_CANONICAL_COLS].reset_index(drop=True)


def load_stack_limits_file(uploaded) -> pd.DataFrame:
    """Читает .properties / .txt / .csv со строками Security code и Max orders."""
    text = _read_text(uploaded)
    if not text.strip():
        raise ValueError("Файл лимитов пустой.")

    if "security" in text.lower() and "code" in text.lower().split("\n")[0:3]:
        table = _parse_delimited_table(text)
        if not table.empty:
            return table

    props = _parse_properties(text)
    if not props.empty:
        return props

    table = _parse_delimited_table(text)
    if not table.empty:
        return table

    raise ValueError(
        "Не удалось разобрать файл. Нужны колонки Security code и Max orders "
        "(таблица через табуляцию/запятую) или свойства code / maxOrders."
    )
