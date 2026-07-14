"""
Анализатор торгов СПбМТСБ (нефтепродукты).
Веб-приложение на Streamlit для загрузки Excel-отчёта по заявкам,
построения инфографики и проверки критериев недобросовестных практик.
"""

from __future__ import annotations

from datetime import time

import streamlit as st

from analytics import (
    basis_distribution,
    check_instrument_limits,
    compute_summary,
    fig_basis_pie,
    fig_corridor_deviations,
    fig_hourly_distribution,
    fig_instruments_limit,
    fig_status_pie,
    fig_top_instruments,
    instruments_order_counts,
    status_breakdown,
)
from criteria import (
    STATUS_BASKET,
    STATUS_INFO,
    STATUS_OK,
    STATUS_POTENTIAL,
    STATUS_VIOLATED,
    build_html_report,
    burst_diagnostics,
    results_to_dataframe,
    run_all_checks,
)
from criteria import _format_sec as format_seconds
from utils import filter_by_session_time, load_excel, time_of_day_to_seconds


# --- Настройки страницы ---
st.set_page_config(
    page_title="Анализатор торгов СПбМТСБ",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Цвета индикаторов результатов
STATUS_STYLE = {
    STATUS_OK: ("✅", "#27ae60"),
    STATUS_VIOLATED: ("❌", "#c0392b"),
    STATUS_POTENTIAL: ("⚠️", "#d35400"),
    STATUS_BASKET: ("🧺", "#f39c12"),
    STATUS_INFO: ("ℹ️", "#2980b9"),
}


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .main-title { font-size: 1.8rem; font-weight: 700; color: #1a5276; margin-bottom: 0.2rem; }
        .sub-title { color: #5d6d7e; margin-bottom: 1.2rem; }
        .metric-card {
            background: linear-gradient(135deg, #f5f8fb 0%, #eaf2f8 100%);
            border: 1px solid #d5e0ea;
            border-radius: 10px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .crit-box {
            border-left: 5px solid #bbb;
            background: #fafbfc;
            padding: 0.75rem 1rem;
            margin-bottom: 0.75rem;
            border-radius: 0 8px 8px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar():
    """Боковая панель: загрузка файла и параметры проверки."""
    st.sidebar.header("Параметры")
    uploaded = st.sidebar.file_uploader(
        "Загрузите Excel (.xlsx)",
        type=["xlsx"],
        help="Файл с заявками СПбМТСБ (колонки: номер, время, код инструмента, направление, цена, статус и др.)",
    )

    st.sidebar.subheader("Интервал анализа")
    filter_enabled = st.sidebar.checkbox(
        "Ограничить время сессии",
        value=True,
        help=(
            "Если включено, заявки вне интервала не участвуют в статистике, "
            "графиках и проверке критериев. По умолчанию 11:00–13:00 — "
            "чтобы исключить пакетную подачу из «Корзины» (обычно около 10:45)."
        ),
    )
    col_t1, col_t2 = st.sidebar.columns(2)
    with col_t1:
        time_from = st.time_input(
            "С",
            value=time(11, 0, 0),
            disabled=not filter_enabled,
        )
    with col_t2:
        time_to = st.time_input(
            "До",
            value=time(13, 0, 0),
            disabled=not filter_enabled,
        )

    use_basket = st.sidebar.checkbox(
        "Использую Корзину заявок",
        value=False,
        help=(
            "Включайте только если заявки подавались через «Корзину заявок». "
            "Тогда критерии №3–№5 получают статус «Исключение», "
            "но фактический результат проверки всё равно показывается в пояснении."
        ),
    )

    instrument_limit = st.sidebar.number_input(
        "Лимит заявок на один инструмент",
        min_value=1,
        max_value=100_000,
        value=250,
        step=10,
        help="Пользовательский лимит заявок на стакан (один код инструмента за день).",
    )

    day_limit_c6 = st.sidebar.number_input(
        "Лимит критерия №6 (заявок/день)",
        min_value=1,
        max_value=100_000,
        value=500,
        step=50,
        help="Пороговое число заявок на покупку по инструменту за торговый день (по умолчанию 500).",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Критерии 1–2 проверяются в упрощённом режиме "
        "(нет данных о встречных заявках и лучшей цене стакана)."
    )
    return (
        uploaded,
        use_basket,
        int(instrument_limit),
        int(day_limit_c6),
        filter_enabled,
        time_from,
        time_to,
    )


def render_summary_banner(summary: dict) -> None:
    """Краткая сводка после успешной загрузки."""
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Заявок", summary["total"])
    c2.metric("Инструментов", summary["instruments"])
    c3.metric("Объём, лотов", f"{summary['total_volume']:.0f}")
    c4.metric("Средний объём", f"{summary['avg_volume']:.2f}")
    c5.metric("Интервал времени", f"{summary['time_min']} — {summary['time_max']}")


def render_tab_stats(df, summary) -> None:
    """Вкладка «Общая статистика»."""
    st.subheader("Общая статистика")
    render_summary_banner(summary)

    st.markdown("##### Распределение по статусам")
    st.dataframe(status_breakdown(df), use_container_width=True, hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### Базисы поставки")
        st.dataframe(basis_distribution(df), use_container_width=True, hide_index=True)
    with col_b:
        st.markdown("##### Статусы (детально)")
        for status, count in summary.get("status_counts", {}).items():
            st.write(f"• **{status}**: {count}")

    with st.expander("Просмотр исходных данных", expanded=False):
        show_cols = [c for c in df.columns if not c.startswith("_") and c not in ("Время_td", "Время_сек")]
        st.dataframe(df[show_cols], use_container_width=True, height=360)


def render_tab_charts(df) -> None:
    """Вкладка «Графики»."""
    st.subheader("Инфографика")

    r1c1, r1c2 = st.columns(2)
    with r1c1:
        st.plotly_chart(fig_status_pie(df), use_container_width=True)
    with r1c2:
        st.plotly_chart(fig_basis_pie(df), use_container_width=True)

    st.plotly_chart(fig_top_instruments(df, top_n=10), use_container_width=True)
    st.plotly_chart(fig_hourly_distribution(df), use_container_width=True)
    st.plotly_chart(fig_corridor_deviations(df), use_container_width=True)

    st.caption(
        "Ценовой коридор: отклонения считаются для заявок с описанием "
        "«Цена заявки выходит за верхнюю границу коридора». "
        "Исполненные/снятые без такого описания учитываются как «в пределах коридора»."
    )


def render_tab_criteria(df, use_basket: bool, day_limit_c6: int) -> list:
    """Вкладка «Проверка критериев». Возвращает список результатов."""
    st.subheader("Проверка критериев недобросовестных торговых практик")

    if use_basket:
        st.warning(
            "Включён чекбокс «Использую Корзину заявок»: критерии №3–№5 "
            "помечены как «Исключение (Корзина)». "
            "Фактические находки (в т.ч. серии за 100 мс) всё равно указаны в пояснении. "
            "Снимите галочку, если корзиной не пользовались — тогда статус станет «Нарушен»/«Не нарушен»."
        )
    else:
        st.caption(
            "Чекбокс «Корзина заявок» выключен — критерии №3–№5 проверяются по данным файла."
        )

    results = run_all_checks(df, use_basket=use_basket, day_limit_c6=day_limit_c6)

    # Диагностика частоты заявок (критерии 3–4)
    diag = burst_diagnostics(df)
    d1, d2, d3 = st.columns(3)
    d1.metric("Макс. покупок за 100 мс", diag["max_100ms"], help="Порог критерия №4: ≥ 3")
    d2.metric("Макс. покупок за 1 с", diag["max_1s"], help="Порог критерия №3: ≥ 7")
    d3.metric(
        "Начало серии 100 мс",
        format_seconds(diag["start_100ms"]),
    )
    if diag["max_100ms"] >= 3:
        st.error(
            f"По сессии обнаружена серия из {diag['max_100ms']} заявок на покупку "
            f"за ≤100 мс (с {format_seconds(diag['start_100ms'])}) — "
            "это основание для критерия №4."
        )

    # Итоговая панель индикаторов
    st.markdown("##### Итоговая панель")
    cols = st.columns(6)
    for i, r in enumerate(results):
        icon, color = STATUS_STYLE.get(r.status, ("•", "#333"))
        with cols[i]:
            st.markdown(
                f"<div class='metric-card' style='border-top:4px solid {color}'>"
                f"<div style='font-size:1.4rem'>{icon}</div>"
                f"<div style='font-weight:600'>№{r.number}</div>"
                f"<div style='color:{color};font-size:0.9rem'>{r.status}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    for r in results:
        _, color = STATUS_STYLE.get(r.status, ("•", "#888"))
        with st.container():
            st.markdown(
                f"<div class='crit-box' style='border-left-color:{color}'>"
                f"<b>Критерий №{r.number}.</b> {r.title}<br/>"
                f"<span style='color:{color};font-weight:700'>{r.status}</span>"
                f"{' · упрощённая проверка' if r.simplified else ''}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.write(r.explanation)
            if r.details:
                with st.expander(f"Детали по критерию №{r.number}"):
                    for d in r.details:
                        st.write(f"• {d}")

    st.markdown("##### Сводная таблица")
    st.dataframe(results_to_dataframe(results), use_container_width=True, hide_index=True)
    return results


def render_tab_limits(df, instrument_limit: int):
    """Вкладка «Лимиты по инструментам»."""
    st.subheader("Лимит заявок на один инструмент (стакан)")
    st.write(
        f"Проверка: количество заявок по каждому коду инструмента за день "
        f"не должно превышать **{instrument_limit}**."
    )

    counts = instruments_order_counts(df)
    violations = check_instrument_limits(df, limit=instrument_limit)

    c1, c2, c3 = st.columns(3)
    c1.metric("Инструментов", len(counts))
    c2.metric("С превышением лимита", len(violations))
    max_cnt = int(counts["Количество"].max()) if not counts.empty else 0
    c3.metric("Максимум заявок", max_cnt)

    if not violations.empty:
        st.error("Обнаружено превышение лимита по следующим инструментам:")
        st.dataframe(violations, use_container_width=True, hide_index=True)
    else:
        st.success(f"Ни один инструмент не превысил лимит {instrument_limit} заявок.")

    st.plotly_chart(fig_instruments_limit(df, limit=instrument_limit), use_container_width=True)

    with st.expander("Полная таблица по инструментам"):
        table = counts.copy()
        table["Превышение"] = table["Количество"].map(
            lambda n: "Да" if n > instrument_limit else "Нет"
        )
        st.dataframe(table, use_container_width=True, hide_index=True)

    return violations


def main() -> None:
    _inject_css()
    st.markdown("<div class='main-title'>Анализатор торгов СПбМТСБ</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-title'>Анализ заявок на нефтепродукты: статистика, графики, "
        "критерии недобросовестных практик и лимиты по инструментам</div>",
        unsafe_allow_html=True,
    )

    (
        uploaded,
        use_basket,
        instrument_limit,
        day_limit_c6,
        filter_enabled,
        time_from,
        time_to,
    ) = render_sidebar()

    if uploaded is None:
        st.info(
            "👈 Загрузите файл Excel (.xlsx) с заявками в боковой панели, "
            "чтобы начать анализ."
        )
        st.markdown(
            """
            **Ожидаемые колонки:** номер заявки, время фиксации, код инструмента,
            направление, цена, статус, наименование инструмента, объёмы,
            описание/информация результата и др.

            **Вкладки после загрузки:**
            1. Общая статистика  
            2. Графики  
            3. Проверка критериев  
            4. Лимиты по инструментам  

            По умолчанию анализируются заявки с **11:00 до 13:00**
            (пакет из «Корзины» около 10:45 исключается).
            """
        )
        return

    # Загрузка и подготовка данных
    try:
        with st.spinner("Чтение и обработка файла…"):
            df_all = load_excel(uploaded)
    except ValueError as exc:
        st.error(f"Ошибка загрузки: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку парсинга
        st.error(f"Непредвиденная ошибка при чтении файла: {exc}")
        return

    if df_all.empty:
        st.warning("Файл загружен, но не содержит строк данных.")
        return

    # Предупреждение о нераспознанном времени
    bad_time = df_all["Время_сек"].isna().sum() if "Время_сек" in df_all.columns else 0
    if bad_time:
        st.warning(
            f"Не удалось распознать время фиксации у {bad_time} из {len(df_all)} заявок. "
            "Проверьте формат колонки «Время фиксации заявки»."
        )

    # Фильтр торговой сессии (исключаем корзину ~10:45 и прочее вне окна)
    if filter_enabled:
        start_sec = time_of_day_to_seconds(time_from.hour, time_from.minute, time_from.second)
        end_sec = time_of_day_to_seconds(time_to.hour, time_to.minute, time_to.second)
        if end_sec <= start_sec:
            st.error("Конец интервала должен быть позже начала.")
            return
        df = filter_by_session_time(df_all, start_sec, end_sec)
        excluded = len(df_all) - len(df)
        interval_label = (
            f"{time_from.strftime('%H:%M:%S')} — {time_to.strftime('%H:%M:%S')}"
        )
        if df.empty:
            st.error(
                f"После фильтра {interval_label} не осталось заявок "
                f"(всего в файле {len(df_all)}, исключено {excluded})."
            )
            return
        st.success(
            f"Файл загружен: **{len(df_all)}** заявок, "
            f"в анализе **{len(df)}** (интервал {interval_label}, "
            f"исключено {excluded}, в т.ч. возможные заявки «Корзины» до 11:00)."
        )
        early = df_all[df_all["Время_сек"].notna() & (df_all["Время_сек"] < start_sec)]
        if len(early):
            st.caption(
                f"До начала интервала отфильтровано {len(early)} заявок "
                f"(мин. время: {format_seconds(float(early['Время_сек'].min()))})."
            )
    else:
        df = df_all
        summary_all = compute_summary(df_all)
        st.success(
            f"Файл загружен: **{summary_all['total']}** заявок "
            f"(фильтр времени выключен), "
            f"время {summary_all['time_min']} — {summary_all['time_max']}."
        )

    summary = compute_summary(df)

    tab_stats, tab_charts, tab_crit, tab_limits = st.tabs(
        ["Общая статистика", "Графики", "Проверка критериев", "Лимиты по инструментам"]
    )

    with tab_stats:
        render_tab_stats(df, summary)

    with tab_charts:
        render_tab_charts(df)

    with tab_crit:
        results = render_tab_criteria(df, use_basket=use_basket, day_limit_c6=day_limit_c6)

    with tab_limits:
        limit_violations = render_tab_limits(df, instrument_limit=instrument_limit)

    # --- Экспорт отчёта ---
    st.markdown("---")
    st.subheader("Отчёт")
    html = build_html_report(
        summary=summary,
        results=results,
        limit_violations=limit_violations,
        instrument_limit=instrument_limit,
    )
    st.download_button(
        label="Скачать отчёт (HTML)",
        data=html.encode("utf-8"),
        file_name="spimex_report.html",
        mime="text/html",
        help="HTML-отчёт со сводкой, результатами критериев и лимитами. Можно открыть в браузере и распечатать.",
    )
    st.caption("Для печати страницы приложения используйте Ctrl+P в браузере.")


if __name__ == "__main__":
    main()
