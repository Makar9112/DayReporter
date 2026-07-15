"""
Вспомогательные функции для анализатора торгов СПбМТСБ.
Парсинг времени, определение базиса поставки, нормализация колонок.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd


# Ожидаемые названия колонок (основные)
REQUIRED_COLUMNS = [
    "Номер заявки",
    "Время фиксации заявки",
    "Код инструмента",
    "Направл.",
    "Цена",
    "Статус",
]

OPTIONAL_COLUMNS = [
    "Наименование инструмента",
    "Вид заявки",
    "Тип заявки",
    "Подтип заявки",
    "Условие исполнения",
    "Объем, лотов",
    "Остаток, лотов",
    "Описание результата",
    "Информация результата",
    "Наименование клиента",
    "Объем, руб.",
    "Объем, нат. ед.",
    "Территориальный код",
]

# Альтернативные варианты названий колонок
COLUMN_ALIASES = {
    "Направл.": ["Направление", "Направл", "Side"],
    "Объем, лотов": ["Объём, лотов", "Объем лотов", "Объём лотов", "VolumeLots"],
    "Остаток, лотов": ["Остаток лотов", "Остаток, лот"],
    "Объем, руб.": ["Объём, руб.", "Объем руб.", "Объём руб."],
    "Объем, нат. ед.": ["Объём, нат. ед.", "Объем нат. ед."],
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Приводит названия колонок к ожидаемому виду через алиасы."""
    rename_map = {}
    cols = list(df.columns)
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in cols:
            continue
        for alias in aliases:
            if alias in cols:
                rename_map[alias] = canonical
                break
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def validate_columns(df: pd.DataFrame) -> Tuple[bool, str]:
    """
    Проверяет наличие обязательных колонок.
    Возвращает (успех, сообщение об ошибке).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return False, (
            f"В файле отсутствуют обязательные колонки: {', '.join(missing)}. "
            f"Найдены колонки: {', '.join(map(str, df.columns))}."
        )
    return True, ""


def parse_time_value(value) -> Optional[timedelta]:
    """
    Парсит время фиксации заявки в timedelta от начала суток.
    Поддерживаемые форматы:
      - ЧЧ:ММ:СС.мс  /  ЧЧ:ММ:СС.ммм
      - ЧЧ:ММ:СС
      - datetime / Timestamp
      - Excel float (доля суток)
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    if isinstance(value, pd.Timestamp):
        return timedelta(
            hours=value.hour,
            minutes=value.minute,
            seconds=value.second,
            microseconds=value.microsecond,
        )

    if isinstance(value, datetime):
        return timedelta(
            hours=value.hour,
            minutes=value.minute,
            seconds=value.second,
            microseconds=value.microsecond,
        )

    if isinstance(value, timedelta):
        return value

    # Числовой формат Excel (доля суток)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total_seconds = float(value) * 24 * 3600
        return timedelta(seconds=total_seconds)

    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "nat"):
        return None

    # ЧЧ:ММ:СС.мс / ЧЧ:ММ:СС,ССС / ЧЧ:ММ:СС
    patterns = [
        # ЧЧ:ММ:СС.миллисекунды (1–6 цифр → мкс)
        r"^(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,6})$",
        r"^(\d{1,2}):(\d{2}):(\d{2})$",
        # Полная дата-время
        r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2}):(\d{2})[.,]?(\d{0,6})$",
        r"^(\d{2})\.(\d{2})\.(\d{4})[ T](\d{1,2}):(\d{2}):(\d{2})[.,]?(\d{0,6})$",
    ]

    m = re.match(patterns[0], text)
    if m:
        h, mi, s, frac = m.groups()
        # Дополняем дробную часть до микросекунд
        frac = (frac + "000000")[:6]
        return timedelta(
            hours=int(h),
            minutes=int(mi),
            seconds=int(s),
            microseconds=int(frac),
        )

    m = re.match(patterns[1], text)
    if m:
        h, mi, s = m.groups()
        return timedelta(hours=int(h), minutes=int(mi), seconds=int(s))

    m = re.match(patterns[2], text)
    if m:
        _y, _mo, _d, h, mi, s, frac = m.groups()
        frac = (frac + "000000")[:6] if frac else "000000"
        return timedelta(
            hours=int(h),
            minutes=int(mi),
            seconds=int(s),
            microseconds=int(frac),
        )

    m = re.match(patterns[3], text)
    if m:
        _d, _mo, _y, h, mi, s, frac = m.groups()
        frac = (frac + "000000")[:6] if frac else "000000"
        return timedelta(
            hours=int(h),
            minutes=int(mi),
            seconds=int(s),
            microseconds=int(frac),
        )

    # Последняя попытка через pandas
    try:
        ts = pd.to_datetime(text, dayfirst=True)
        if pd.notna(ts):
            return timedelta(
                hours=ts.hour,
                minutes=ts.minute,
                seconds=ts.second,
                microseconds=ts.microsecond,
            )
    except Exception:
        pass

    return None


def parse_times_column(series: pd.Series) -> pd.Series:
    """Преобразует колонку времени в timedelta. Нераспознанные значения → NaT-аналог (None)."""
    return series.map(parse_time_value)


def timedelta_to_seconds(td) -> Optional[float]:
    """Преобразует timedelta в секунды (float) с миллисекундами."""
    if td is None or (isinstance(td, float) and pd.isna(td)):
        return None
    if isinstance(td, timedelta):
        return td.total_seconds()
    return None


def format_timedelta(td) -> str:
    """Форматирует timedelta как ЧЧ:ММ:СС.ммм."""
    if td is None or (isinstance(td, float) and pd.isna(td)):
        return "—"
    if not isinstance(td, timedelta):
        return str(td)
    total_ms = int(td.total_seconds() * 1000)
    hours = total_ms // 3_600_000
    rem = total_ms % 3_600_000
    minutes = rem // 60_000
    rem = rem % 60_000
    seconds = rem // 1000
    ms = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def detect_basis(instrument_code: str, instrument_name: str = "") -> str:
    """
    Определяет базис поставки по коду инструмента и наименованию.

    Правила:
      - код оканчивается на J → франко-вагон станция отправления ОТП
      - код оканчивается на F → франко-вагон станция отправления
      - иначе — по подстрокам в наименовании (труба / вагон / ОТП)
    """
    code = str(instrument_code).strip().upper() if instrument_code is not None else ""
    name = str(instrument_name).lower() if instrument_name is not None else ""

    if code.endswith("J"):
        return "франко-вагон станция отправления ОТП"
    if code.endswith("F"):
        return "франко-вагон станция отправления"

    # Эвристика по наименованию
    if "труба" in name or "pipeline" in name or code.endswith("T"):
        return "франко-труба"
    if "отп" in name:
        return "франко-вагон станция отправления ОТП"
    if "вагон" in name or "франко-вагон" in name:
        return "франко-вагон станция отправления"

    return "не определён"


def add_basis_column(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет колонку «Базис» на основе кода и наименования инструмента."""
    name_col = "Наименование инструмента" if "Наименование инструмента" in df.columns else None

    def _row_basis(row) -> str:
        name = row[name_col] if name_col else ""
        return detect_basis(row.get("Код инструмента", ""), name)

    result = df.copy()
    result["Базис"] = result.apply(_row_basis, axis=1)
    return result


def extract_corridor_bound(info_text) -> Optional[float]:
    """
    Извлекает числовое значение границы коридора из «Информация результата».
    Ищет первое число с возможной десятичной точкой/запятой.
    """
    if info_text is None or (isinstance(info_text, float) and pd.isna(info_text)):
        return None
    text = str(info_text).replace("\xa0", " ").replace(" ", "")
    # Число вида 12345.67 или 12345,67
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def is_above_corridor(description) -> bool:
    """Проверяет, выходит ли цена за верхнюю границу коридора по описанию результата."""
    if description is None or (isinstance(description, float) and pd.isna(description)):
        return False
    text = str(description).lower()
    return "верхн" in text and "коридор" in text


def is_buy(direction) -> bool:
    """Проверяет, является ли направление покупкой."""
    if direction is None:
        return False
    text = str(direction).strip().lower()
    return text in ("покупка", "buy", "b", "п")


def is_executed(status) -> bool:
    """Проверяет статус «Исполнена»."""
    if status is None:
        return False
    return "исполнен" in str(status).strip().lower()


def is_cancelled_or_withdrawn(status) -> bool:
    """Проверяет, что заявка отменена / снята."""
    if status is None:
        return False
    text = str(status).strip().lower()
    return any(k in text for k in ("отменен", "отменён", "снят"))


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Полная подготовка датафрейма после загрузки:
    нормализация колонок, парсинг времени, базис, числовые поля.
    """
    df = normalize_columns(df)
    ok, msg = validate_columns(df)
    if not ok:
        raise ValueError(msg)

    result = df.copy()
    result["Время_td"] = parse_times_column(result["Время фиксации заявки"])
    result["Время_сек"] = result["Время_td"].map(timedelta_to_seconds)

    # Числовые колонки
    for col in ("Цена", "Объем, лотов", "Остаток, лотов", "Объем, руб.", "Объем, нат. ед."):
        if col in result.columns:
            result[col] = pd.to_numeric(
                result[col].astype(str).str.replace(",", ".", regex=False).str.replace("\xa0", "", regex=False).str.replace(" ", "", regex=False),
                errors="coerce",
            )

    result = add_basis_column(result)
    result["_is_buy"] = result["Направл."].map(is_buy)
    result["_is_executed"] = result["Статус"].map(is_executed)

    if "Описание результата" in result.columns:
        result["_above_corridor"] = result["Описание результата"].map(is_above_corridor)
    else:
        result["_above_corridor"] = False

    if "Информация результата" in result.columns:
        result["_corridor_bound"] = result["Информация результата"].map(extract_corridor_bound)
    else:
        result["_corridor_bound"] = None

    return result


def filter_by_session_time(
    df: pd.DataFrame,
    start_seconds: float,
    end_seconds: float,
) -> pd.DataFrame:
    """
    Оставляет заявки с Время_сек в полуинтервале [start_seconds, end_seconds).
    Строки без распознанного времени исключаются.
    """
    if df.empty or "Время_сек" not in df.columns:
        return df.copy()
    mask = df["Время_сек"].notna() & (df["Время_сек"] >= start_seconds) & (df["Время_сек"] < end_seconds)
    return df.loc[mask].copy()


def time_of_day_to_seconds(h: int, m: int = 0, s: int = 0) -> float:
    """Часы/минуты/секунды → секунды от начала суток."""
    return float(h * 3600 + m * 60 + s)


def _read_uploaded_bytes(uploaded_file) -> bytes:
    """Читает байты из UploadedFile / path / BytesIO."""
    if hasattr(uploaded_file, "getvalue"):
        data = uploaded_file.getvalue()
        # после getvalue сбрасываем указатель — на случай повторного чтения
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass
        return data
    if hasattr(uploaded_file, "read"):
        data = uploaded_file.read()
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    # путь к файлу
    from pathlib import Path

    return Path(uploaded_file).read_bytes()


def _validate_excel_bytes(data: bytes) -> None:
    """
    Проверяет, что буфер похож на настоящий .xlsx (ZIP).
    Частая проблема: «битые» файлы из папки (все нули) или не Excel под расширением .xlsx.
    """
    import zipfile
    from io import BytesIO

    if not data:
        raise ValueError(
            "Файл пустой (0 байт). Выгрузите журнал заявок из торгового терминала заново."
        )

    if not any(data):
        raise ValueError(
            "Файл повреждён: содержимое полностью пустое (нулевые байты), это не Excel. "
            "Так бывает при сбое копирования/синхронизации OneDrive. "
            "Откройте исходный файл на биржевом ПК, сохраните как .xlsx "
            "(«Файл → Сохранить как») и загрузите эту копию."
        )

    # .xlsx / .xlsm — это ZIP (сигнатура PK)
    if data[:2] != b"PK":
        head = data.lstrip()[:80].lower()
        if head.startswith(b"<html") or b"<table" in head:
            raise ValueError(
                "Похоже, это HTML, сохранённый с расширением .xlsx. "
                "Откройте файл в Excel и сохраните в формате "
                "«Книга Excel (*.xlsx)»."
            )
        if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError(
                "Это старый формат .xls. Откройте файл в Excel и сохраните как .xlsx."
            )
        raise ValueError(
            "Файл не является корректным .xlsx (ожидается ZIP-архив Excel). "
            "Откройте его в Microsoft Excel — если не открывается, пересохраните "
            "журнал заявок из терминала СПбМТСБ в формате .xlsx."
        )

    if not zipfile.is_zipfile(BytesIO(data)):
        raise ValueError(
            "Файл имеет сигнатуру ZIP, но архив повреждён. "
            "Пересохраните отчёт в Excel как .xlsx и загрузите снова."
        )


def load_excel(uploaded_file) -> pd.DataFrame:
    """Читает Excel-файл (.xlsx) и возвращает подготовленный DataFrame."""
    from io import BytesIO

    try:
        data = _read_uploaded_bytes(uploaded_file)
        _validate_excel_bytes(data)
        raw = pd.read_excel(BytesIO(data), engine="openpyxl")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Не удалось прочитать Excel-файл: {exc}. "
            "Убедитесь, что это настоящий .xlsx (откройте в Excel и при необходимости "
            "«Сохранить как» → Книга Excel)."
        ) from exc

    if raw.empty:
        raise ValueError("Файл не содержит данных.")

    return prepare_dataframe(raw)
