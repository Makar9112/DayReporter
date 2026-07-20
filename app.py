"""
Анализатор торгов СПбМТСБ (нефтепродукты).
Веб-приложение на Streamlit для загрузки Excel-отчёта по заявкам,
построения инфографики и проверки критериев недобросовестных практик.
"""

from __future__ import annotations

import io
from datetime import datetime, time

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
    burst_diagnostics,
    results_to_dataframe,
    run_all_checks,
)
from criteria import _format_sec as format_seconds
from rhythm_guide import (
    fig_click_metronome,
    fig_rhythm_howto_100ms,
    fig_rhythm_howto_1s,
    fig_user_gaps_vs_safe,
    recommended_click_instruction,
)
from strategy_advisor import (
    PRIORITY_HIGH,
    PRIORITY_LABELS,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    build_strategy_report,
    tips_to_dataframe,
)
from contracts_lag import (
    fig_lag_histogram,
    lag_summary,
    load_contracts_excel,
    match_orders_to_contracts,
)
from report_export import build_full_html_report
from utils import (
    MEDIAN_HELP,
    MEDIAN_HELP_SHORT,
    filter_by_session_time,
    load_excel,
    time_of_day_to_seconds,
)


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
        .tip-box {
            border-left: 5px solid #bbb;
            background: #fafbfc;
            padding: 0.75rem 1rem;
            margin-bottom: 0.65rem;
            border-radius: 0 8px 8px 0;
        }
        .plan-box {
            background: #eafaf1;
            border: 1px solid #abebc6;
            border-radius: 8px;
            padding: 0.85rem 1rem;
            margin-top: 0.5rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar():
    """Боковая панель: загрузка файла и параметры проверки."""
    st.sidebar.header("Параметры")
    uploaded = st.sidebar.file_uploader(
        "Журнал заявок (.xlsx)",
        type=["xlsx"],
        help="Файл с заявками СПбМТСБ (колонки: номер, время, код инструмента, направление, цена, статус и др.)",
        key=f"excel_uploader_{st.session_state.get('uploader_reset', 0)}",
    )

    uploaded_contracts = st.sidebar.file_uploader(
        "Отчёт по договорам (.xlsx)",
        type=["xlsx"],
        help="Выгрузка «Договоры»: Время договора, Код инструмента, Цена, Объем, лотов и др.",
        key=f"contracts_uploader_{st.session_state.get('contracts_uploader_reset', 0)}",
    )

    if st.session_state.get("df_all") is not None:
        name = st.session_state.get("upload_name", "файл")
        st.sidebar.caption(f"Заявки: **{name}**")
    if st.session_state.get("df_contracts") is not None:
        cname = st.session_state.get("contracts_upload_name", "договоры")
        st.sidebar.caption(f"Договоры: **{cname}**")

    if st.session_state.get("df_all") is not None or st.session_state.get("df_contracts") is not None:
        if st.sidebar.button("Очистить загруженные данные", use_container_width=True):
            for key in (
                "df_all",
                "upload_name",
                "upload_key",
                "upload_bytes",
                "df_contracts",
                "contracts_upload_name",
                "contracts_upload_key",
            ):
                st.session_state.pop(key, None)
            st.session_state["uploader_reset"] = st.session_state.get("uploader_reset", 0) + 1
            st.session_state["contracts_uploader_reset"] = (
                st.session_state.get("contracts_uploader_reset", 0) + 1
            )
            st.rerun()

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
    max_lag_sec = st.sidebar.number_input(
        "Окно: договор до заявки, с",
        min_value=1,
        max_value=600,
        value=120,
        step=10,
        help=(
            "Для каждой вашей заявки ищется последний договор по тому же инструменту "
            "не раньше чем за столько секунд до фиксации заявки."
        ),
    )

    return (
        uploaded,
        uploaded_contracts,
        use_basket,
        int(instrument_limit),
        int(day_limit_c6),
        filter_enabled,
        time_from,
        time_to,
        float(max_lag_sec),
    )


def ensure_dataframe_loaded(uploaded) -> bool:
    """
    Загружает Excel в session_state и удерживает его между rerun'ами
    (клик «Скачать отчёт» и смена параметров не сбрасывают данные).
    Возвращает True, если данные для анализа есть.
    """
    if uploaded is not None:
        file_key = (uploaded.name, uploaded.size)
        if st.session_state.get("upload_key") != file_key:
            try:
                raw = uploaded.getvalue()
                with st.spinner("Чтение и обработка файла…"):
                    df_all = load_excel(io.BytesIO(raw))
            except ValueError as exc:
                st.error(f"Ошибка загрузки: {exc}")
                return False
            except Exception as exc:  # noqa: BLE001
                st.error(f"Непредвиденная ошибка при чтении файла: {exc}")
                return False

            if df_all.empty:
                st.warning("Файл загружен, но не содержит строк данных.")
                return False

            st.session_state["upload_key"] = file_key
            st.session_state["upload_name"] = uploaded.name
            st.session_state["upload_bytes"] = raw
            st.session_state["df_all"] = df_all

    return st.session_state.get("df_all") is not None


def ensure_contracts_loaded(uploaded_contracts) -> bool:
    """Загружает отчёт по договорам в session_state."""
    if uploaded_contracts is not None:
        file_key = (uploaded_contracts.name, uploaded_contracts.size)
        if st.session_state.get("contracts_upload_key") != file_key:
            try:
                with st.spinner("Чтение отчёта по договорам…"):
                    df_c = load_contracts_excel(uploaded_contracts)
            except ValueError as exc:
                st.error(f"Ошибка загрузки договоров: {exc}")
                return False
            except Exception as exc:  # noqa: BLE001
                st.error(f"Непредвиденная ошибка при чтении договоров: {exc}")
                return False

            if df_c.empty:
                st.warning("Файл договоров пуст.")
                return False

            st.session_state["contracts_upload_key"] = file_key
            st.session_state["contracts_upload_name"] = uploaded_contracts.name
            st.session_state["df_contracts"] = df_c

    return st.session_state.get("df_contracts") is not None


def render_tab_contract_lag(df_orders, df_contracts, max_lag_sec: float) -> None:
    """Вкладка: задержка между фиксацией заявки и временем договора."""
    st.subheader("Задержка заявка → договор")
    st.caption(
        "Для **каждой вашей заявки** (покупка) берётся **последний договор по тому же инструменту**, "
        "который был **до** времени фиксации заявки. "
        "**Задержка реакции** = время заявки − время этого договора (мс и с). "
        "Цена и лоты совпадать не обязаны — вы реагируете на сделку, а заявку выставляете со своими параметрами."
    )

    bad_c = df_contracts["Время_сек"].isna().sum() if "Время_сек" in df_contracts.columns else 0
    if bad_c:
        st.warning(f"Не распознано время договора у {bad_c} строк.")

    matched, meta = match_orders_to_contracts(
        df_orders,
        df_contracts,
        max_lag_sec=max_lag_sec,
    )
    summary = lag_summary(matched)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ваших заявок", meta.get("orders_total", 0))
    c2.metric("Договоров в файле", meta.get("contracts_total", 0))
    c3.metric("С оценкой задержки", meta.get("matched", 0))
    c4.metric("Без договора в окне", meta.get("unmatched_orders", 0))
    c5.metric("До 1-го договора", meta.get("orders_before_first_contract", 0))

    after_count = summary.get("after_count", 0)
    if after_count:
        st.markdown("##### Оценка задержки отправки заявки")
        st.caption(
            "Ниже только те случаи, где ваша заявка зарегистрирована **после** сделки. "
            "Цель: стремиться к минимальному значению."
        )

        def fmt_ms_and_sec(value):
            if value in (None, "—"):
                return "—"
            return f"{value} мс ({round(float(value) / 1000, 3)} с)"

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Минимум", fmt_ms_and_sec(summary.get("after_min_ms")))
        s2.metric(
            "Медиана",
            fmt_ms_and_sec(summary.get("after_median_ms")),
            help=MEDIAN_HELP_SHORT,
        )
        s3.metric(
            "Среднее",
            fmt_ms_and_sec(summary.get("after_mean_ms")),
            help="Среднее арифметическое всех задержек; сильнее реагирует на редкие очень долгие реакции.",
        )
        s4.metric(
            "90%",
            fmt_ms_and_sec(summary.get("after_p90_ms")),
            help="90% задержек быстрее этого значения; только 10% — медленнее.",
        )
        s5.metric("Максимум", fmt_ms_and_sec(summary.get("after_max_ms")))
        st.caption(MEDIAN_HELP)

        best_row = matched.sort_values("Задержка реакции, мс").iloc[0]
        st.success(
            "Минимальная задержка в сессии: "
            f"договор **{best_row['Время договора']}**, заявка **{best_row['Время заявки']}** "
            f"({best_row['Код инструмента']}). "
            f"Задержка **{fmt_ms_and_sec(best_row['Задержка реакции, мс'])}**."
        )
        st.plotly_chart(fig_lag_histogram(matched), use_container_width=True)
    else:
        st.info(
            "Не удалось оценить задержку: нет договоров по вашим инструментам в окне перед заявками "
            f"(см. параметр «Окно: договор до заявки» — сейчас {max_lag_sec:.0f} с), "
            "или все заявки были до первого договора по инструменту."
        )

    if not matched.empty:
        st.markdown("##### Таблица сопоставлений")
        table = matched.copy()
        table["Задержка, с"] = table["Задержка реакции, мс"].map(
            lambda v: round(float(v) / 1000, 3)
        )
        st.dataframe(
            table.sort_values("Задержка реакции, мс"),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Как читать результат"):
        st.markdown(
            """
- Договор `11:35:40.830`, ваша заявка `11:35:41.300` → задержка **470 мс (0.470 с)**.
- Опорный договор — **последняя сделка по инструменту перед вашей заявкой**, а не обязательно с той же ценой.
- Цель — уменьшать **минимум** и медиану задержки реакции.
- Заявки до первого договора по инструменту в отчёт не попадают (счётчик «До 1-го договора»).
- Оценка по временам из выгрузок терминала, не замер сети «клик → биржа».
            """
        )
        st.markdown(MEDIAN_HELP)


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


TIP_STYLE = {
    PRIORITY_HIGH: ("🔴", "#c0392b"),
    PRIORITY_MEDIUM: ("🟡", "#d68910"),
    PRIORITY_LOW: ("🟢", "#27ae60"),
}


def render_tab_recommendations(
    df,
    results: list,
    instrument_limit: int,
    day_limit_c6: int,
) -> None:
    """Вкладка «Рекомендации» — советы по ритму и стратегии."""
    st.subheader("Рекомендации по торговой стратегии")
    st.caption(
        "Советы на основе ритма подачи заявок, лимитов и результатов проверки критериев. "
        "Это операционные подсказки, а не гарантия лучшей цены на рынке."
    )

    report = build_strategy_report(
        df,
        results,
        instrument_limit=instrument_limit,
        day_limit_c6=day_limit_c6,
    )
    stats = report.rhythm_stats
    howto = recommended_click_instruction(stats)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Индекс дисциплины", f"{report.discipline_score}/100")
    c2.metric("Оценка ритма", report.rhythm_grade)
    c3.metric("Макс. / 100 мс", stats.get("max_100ms", 0), help="Безопасно: ≤2")
    c4.metric("Макс. / 1 с", stats.get("max_1s", 0), help="Безопасно: ≤5")

    if stats.get("gap_median_ms") is not None:
        g1, g2, g3 = st.columns(3)
        g1.metric(
            "Медиана паузы",
            f"{stats['gap_median_ms']} мс",
            help=MEDIAN_HELP_SHORT,
        )
        g2.metric("Мин. пауза", f"{stats['gap_min_ms']} мс")
        g3.metric("Пауз < 100 мс", stats.get("gaps_under_100ms", 0))

    # --- Шпаргалка «как нажимать» ---
    st.markdown("---")
    st.markdown("##### Как нажимать кнопку покупки")

    if howto["mode"] == "slow_down":
        st.error(howto["pace"])
    elif howto["mode"] == "caution":
        st.warning(howto["pace"])
    else:
        st.success(howto["pace"])

    st.markdown(
        f"<div class='plan-box'>"
        f"<p style='margin:0.2rem 0'><b>Три правила:</b> {howto['rule_short']}</p>"
        f"<p style='margin:0.45rem 0 0'>{howto['count_trick']}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.plotly_chart(fig_click_metronome(), use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(fig_rhythm_howto_100ms(), use_container_width=True)
        st.caption("Красная зона 0–100 мс: третья заявка в этом окне = риск критерия №4.")
    with col_b:
        st.plotly_chart(fig_rhythm_howto_1s(), use_container_width=True)
        st.caption("За одну секунду лучше не больше 4–5 кликов (порог нарушения — 7).")

    st.plotly_chart(fig_user_gaps_vs_safe(stats, df), use_container_width=True)
    st.caption(
        "Зелёная линия — целевая пауза ≥150 мс. "
        "Всё левее красной точки (<100 мс) — опасный «залп» кликов."
    )

    with st.expander("Пошагово: что делать руками в терминале", expanded=False):
        st.markdown(
            """
1. **Выбрали инструмент** — не торопитесь «настрелять» заявками.
2. **Клик покупки** — отправили одну заявку.
3. **Пауза** — скажите про себя «и-раз» (~200 мс) или счёт «раз-и».
4. **Следующий клик** — только после паузы.
5. **Не больше двух кликов** в любые 100 мс подряд.
6. **Не больше пяти кликов** в любую 1 секунду.
7. **Сменили инструмент** — ритм считайте заново с нуля для него, но общую сессию тоже не «долбите».

**Опасно**
- Три быстрых клика подряд «чтобы успеть» (за <100 мс).
- Семь и более кликов за секунду по одному инструменту.

**Безопасно**
- Ровный темп: клик → пауза 200 мс → клик → пауза…
- Лучше чуть медленнее, чем один раз сорваться к критерию №4.
            """
        )

    st.markdown("##### План на следующую сессию")
    st.markdown(
        "<div class='plan-box'>"
        + "".join(f"<p style='margin:0.35rem 0'>• {p}</p>" for p in report.session_plan)
        + "</div>",
        unsafe_allow_html=True,
    )

    high = [t for t in report.tips if t.priority == PRIORITY_HIGH]
    if high:
        st.error(f"Высокий приоритет: {len(high)} рекомендаций — стоит скорректировать ритм в первую очередь.")

    st.markdown("##### Карточки рекомендаций")
    if not report.tips:
        st.success("Замечаний по ритму нет. Текущий темп выглядит дисциплинированным.")
    else:
        for tip in report.tips:
            icon, color = TIP_STYLE.get(tip.priority, ("•", "#888"))
            prio_label = PRIORITY_LABELS.get(tip.priority, tip.priority)
            st.markdown(
                f"<div class='tip-box' style='border-left-color:{color}'>"
                f"<b>{icon} {tip.title}</b> "
                f"<span style='color:{color};font-size:0.85rem'>· {prio_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.write(tip.message)
            st.caption(f"Основание: {tip.evidence}")
            st.info(f"**Действие:** {tip.action}")

    with st.expander("Сводная таблица рекомендаций"):
        st.dataframe(tips_to_dataframe(report.tips), use_container_width=True, hide_index=True)

    with st.expander("Справка: безопасные ориентиры"):
        st.markdown(
            """
            | Параметр | Порог нарушения | Безопасная зона |
            |----------|-----------------|-----------------|
            | Критерий №4 | ≥ 3 покупки / 100 мс | ≤ 2 / 100 мс, пауза ≥ 150 мс |
            | Критерий №3 | ≥ 7 покупок / 1 с | ≤ 5 / 1 с, пауза ≥ 1,2 с |
            | Лимит стакана | > 250 / инструмент | ≤ 200 (запас 20%) |
            | Критерий №6 | > 500 покупок / день | ≤ 400 по инструменту |
            """
        )


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
        "критерии недобросовестных практик, лимиты и рекомендации по стратегии</div>",
        unsafe_allow_html=True,
    )

    (
        uploaded,
        uploaded_contracts,
        use_basket,
        instrument_limit,
        day_limit_c6,
        filter_enabled,
        time_from,
        time_to,
        max_lag_sec,
    ) = render_sidebar()

    ensure_contracts_loaded(uploaded_contracts)

    if not ensure_dataframe_loaded(uploaded):
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
            5. Рекомендации по стратегии  
            6. **Задержка до договора** (нужен второй файл — отчёт по договорам)  

            По умолчанию анализируются заявки с **11:00 до 13:00**
            (пакет из «Корзины» около 10:45 исключается).
            """
        )
        return

    df_all = st.session_state["df_all"]

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
            f"Файл: **{st.session_state.get('upload_name', 'отчёт')}** — "
            f"**{len(df_all)}** заявок, в анализе **{len(df)}** "
            f"(интервал {interval_label}, исключено {excluded})."
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
            f"Файл: **{st.session_state.get('upload_name', 'отчёт')}** — "
            f"**{summary_all['total']}** заявок (фильтр времени выключен), "
            f"время {summary_all['time_min']} — {summary_all['time_max']}."
        )

    summary = compute_summary(df)
    if filter_enabled:
        session_interval = (
            f"{time_from.strftime('%H:%M:%S')} — {time_to.strftime('%H:%M:%S')}"
        )
    else:
        session_interval = "без фильтра времени"

    tab_stats, tab_charts, tab_crit, tab_limits, tab_advice, tab_lag = st.tabs(
        [
            "Общая статистика",
            "Графики",
            "Проверка критериев",
            "Лимиты по инструментам",
            "Рекомендации",
            "Задержка до договора",
        ]
    )

    with tab_stats:
        render_tab_stats(df, summary)

    with tab_charts:
        render_tab_charts(df)

    with tab_crit:
        results = render_tab_criteria(df, use_basket=use_basket, day_limit_c6=day_limit_c6)

    with tab_limits:
        limit_violations = render_tab_limits(df, instrument_limit=instrument_limit)

    with tab_advice:
        render_tab_recommendations(
            df,
            results,
            instrument_limit=instrument_limit,
            day_limit_c6=day_limit_c6,
        )

    with tab_lag:
        if st.session_state.get("df_contracts") is None:
            st.info(
                "Загрузите **отчёт по договорам** (.xlsx) в боковой панели — "
                "колонки: «Время договора», «Код инструмента», «Цена», «Объем, лотов»."
            )
        else:
            render_tab_contract_lag(
                df,
                st.session_state["df_contracts"],
                max_lag_sec=max_lag_sec,
            )

    # --- Экспорт отчёта ---
    st.markdown("---")
    st.subheader("Отчёт")
    report_html = build_full_html_report(
        summary=summary,
        df=df,
        results=results,
        limit_violations=limit_violations,
        instrument_limit=instrument_limit,
        day_limit_c6=day_limit_c6,
        use_basket=use_basket,
        upload_name=st.session_state.get("upload_name", ""),
        contracts_name=st.session_state.get("contracts_upload_name", ""),
        session_interval=session_interval,
        df_contracts=st.session_state.get("df_contracts"),
        max_lag_sec=max_lag_sec,
    )
    report_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    st.download_button(
        label="Скачать полный отчёт (HTML)",
        data=report_html.encode("utf-8"),
        file_name=f"spimex_report_{report_stamp}.html",
        mime="text/html",
        key="download_html_report",
        help=(
            "Единый HTML-файл: статистика, графики, критерии, лимиты, "
            "рекомендации и задержка до договора. Удобно читать в браузере и печатать (Ctrl+P)."
        ),
    )
    st.caption(
        "Отчёт содержит все вкладки в одном документе с оглавлением и таблицами. "
        "Для графиков при открытии файла нужен интернет (библиотека Plotly)."
    )


if __name__ == "__main__":
    main()
