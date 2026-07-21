"""
Формирование полного HTML-отчёта по всем разделам приложения.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.io as pio

from analytics import (
    basis_distribution,
    fig_basis_pie,
    fig_corridor_deviations,
    fig_hourly_distribution,
    fig_instruments_limit,
    fig_status_pie,
    fig_top_instruments,
    instruments_order_counts,
    status_breakdown,
)
from basis_fill_charts import fig_instruments_limit_with_basis_fill
from contracts_lag import (
    aggregate_basis_fill_by_instrument,
    fig_lag_histogram,
    lag_summary,
    match_orders_to_contracts,
)
from criteria import (
    STATUS_BASKET,
    STATUS_INFO,
    STATUS_OK,
    STATUS_POTENTIAL,
    STATUS_VIOLATED,
    CriterionResult,
    burst_diagnostics,
    results_to_dataframe,
)
from strategy_advisor import PRIORITY_LABELS, build_strategy_report, tips_to_dataframe
from help_texts import MEDIAN_HELP_PLAIN

_STATUS_COLORS = {
    STATUS_VIOLATED: "#c0392b",
    STATUS_POTENTIAL: "#d35400",
    STATUS_OK: "#27ae60",
    STATUS_BASKET: "#f39c12",
    STATUS_INFO: "#2980b9",
}


def _esc(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return html.escape(str(value))


def _df_table(df: pd.DataFrame, *, max_rows: Optional[int] = None) -> str:
    if df is None or df.empty:
        return '<p class="muted">Нет данных.</p>'
    work = df.head(max_rows) if max_rows else df
    table_html = work.to_html(
        index=False,
        border=0,
        classes="data-table",
        escape=True,
    )
    if max_rows and len(df) > max_rows:
        table_html += (
            f'<p class="muted">Показано {max_rows} из {len(df)} строк.</p>'
        )
    return table_html


def _plot_div(fig, div_id: str) -> str:
    if fig is None:
        return ""
    return pio.to_html(
        fig,
        full_html=False,
        include_plotlyjs=False,
        div_id=div_id,
        config={"displayModeBar": False, "responsive": True},
    )


def _metrics_grid(items: List[tuple[str, str]]) -> str:
    cells = "".join(
        f'<div class="metric"><div class="metric-label">{_esc(k)}</div>'
        f'<div class="metric-value">{_esc(v)}</div></div>'
        for k, v in items
    )
    return f'<div class="metrics">{cells}</div>'


def _section(title: str, section_id: str, body: str) -> str:
    return f"""
<section class="card" id="{section_id}">
  <h2>{_esc(title)}</h2>
  {body}
</section>
"""


def _fmt_ms_sec(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    return f"{ms} мс ({round(float(ms) / 1000, 3)} с)"


def build_full_html_report(
    *,
    summary: Dict[str, Any],
    df: pd.DataFrame,
    results: List[CriterionResult],
    limit_violations: pd.DataFrame,
    instrument_limit: int,
    day_limit_c6: int,
    use_basket: bool,
    upload_name: str = "",
    contracts_name: str = "",
    session_interval: str = "",
    df_contracts: Optional[pd.DataFrame] = None,
    df_contracts_session: Optional[pd.DataFrame] = None,
    max_lag_sec: float = 120.0,
) -> str:
    """Полный отчёт: статистика, графики, критерии, лимиты, рекомендации, задержка."""
    generated = datetime.now().strftime("%d.%m.%Y %H:%M")
    diag = burst_diagnostics(df)
    strategy = build_strategy_report(
        df,
        results,
        instrument_limit=instrument_limit,
        day_limit_c6=day_limit_c6,
    )
    inst_counts = instruments_order_counts(df)

    # --- Критерии ---
    crit_rows = ""
    for r in results:
        color = _STATUS_COLORS.get(r.status, "#333")
        details = ""
        if r.details:
            details = "<ul>" + "".join(f"<li>{_esc(d)}</li>" for d in r.details) + "</ul>"
        crit_rows += f"""
        <tr>
          <td>{r.number}</td>
          <td>{_esc(r.title)}</td>
          <td style="color:{color};font-weight:700">{_esc(r.status)}</td>
          <td>{_esc(r.explanation)}{details}</td>
        </tr>
        """

    # --- Графики ---
    charts_html = ""
    fill_for_limits = None
    if df_contracts_session is not None and not df_contracts_session.empty:
        fill_for_limits = aggregate_basis_fill_by_instrument(df_contracts_session)
    limits_fig = fig_instruments_limit(df, limit=instrument_limit)
    if fill_for_limits is not None and not fill_for_limits.empty:
        limits_fig = fig_instruments_limit_with_basis_fill(
            df,
            fill_for_limits,
            limit=instrument_limit,
            variant="dual_bars",
        )
    chart_specs = [
        ("chart-status", fig_status_pie(df), "Статусы заявок"),
        ("chart-basis", fig_basis_pie(df), "Базисы поставки"),
        ("chart-top", fig_top_instruments(df, top_n=10), "Топ инструментов"),
        ("chart-hour", fig_hourly_distribution(df), "Распределение по времени"),
        ("chart-corridor", fig_corridor_deviations(df), "Коридор цен"),
        ("chart-limits", limits_fig, "Лимиты по инструментам"),
    ]
    for div_id, fig, caption in chart_specs:
        div = _plot_div(fig, div_id)
        if div:
            charts_html += f'<figure class="chart-block"><figcaption>{_esc(caption)}</figcaption>{div}</figure>'

    # --- Задержка до договора ---
    lag_section = ""
    if df_contracts is not None and not df_contracts.empty:
        matched, lag_meta = match_orders_to_contracts(
            df, df_contracts, max_lag_sec=max_lag_sec
        )
        lag_sum = lag_summary(matched)
        lag_metrics = _metrics_grid(
            [
                ("Ваших заявок", str(lag_meta.get("orders_total", 0))),
                ("Договоров в файле", str(lag_meta.get("contracts_total", 0))),
                ("С оценкой задержки", str(lag_meta.get("matched", 0))),
                ("Минимум", _fmt_ms_sec(lag_sum.get("after_min_ms"))),
                ("Медиана", _fmt_ms_sec(lag_sum.get("after_median_ms"))),
                ("90%", _fmt_ms_sec(lag_sum.get("after_p90_ms"))),
            ]
        )
        lag_table = _df_table(matched.sort_values("Задержка реакции, мс"), max_rows=150)
        lag_chart = _plot_div(fig_lag_histogram(matched), "chart-lag")
        lag_section = _section(
            "6. Задержка до договора",
            "lag",
            f"""
            <p class="lead">Для каждой покупки: время заявки минус время последнего договора по инструменту.</p>
            <p class="muted">Файл договоров: <b>{_esc(contracts_name or "—")}</b>. Окно поиска договора: {max_lag_sec:.0f} с.</p>
            {lag_metrics}
            <div class="note-box">{_esc(MEDIAN_HELP_PLAIN)}</div>
            {lag_chart}
            <h3>Таблица задержек</h3>
            {lag_table}
            """,
        )
    else:
        lag_section = _section(
            "6. Задержка до договора",
            "lag",
            '<p class="muted">Отчёт по договорам не загружался — раздел пропущен.</p>',
        )

    # --- Рекомендации ---
    tips_df = tips_to_dataframe(strategy.tips)
    tips_list = ""
    for tip in strategy.tips:
        prio = PRIORITY_LABELS.get(tip.priority, tip.priority)
        tips_list += f"""
        <div class="tip">
          <div class="tip-title">{_esc(tip.title)} <span class="tag">{_esc(prio)}</span></div>
          <p>{_esc(tip.message)}</p>
          <p class="muted"><b>Действие:</b> {_esc(tip.action)}</p>
        </div>
        """
    plan_items = "".join(f"<li>{_esc(p)}</li>" for p in strategy.session_plan)

    stats = strategy.rhythm_stats
    advice_body = f"""
    {_metrics_grid([
        ("Индекс дисциплины", f"{strategy.discipline_score}/100"),
        ("Оценка ритма", strategy.rhythm_grade),
        ("Макс. / 100 мс", str(stats.get("max_100ms", 0))),
        ("Макс. / 1 с", str(stats.get("max_1s", 0))),
        ("Медиана паузы", _fmt_ms_sec(stats.get("gap_median_ms"))),
    ])}
    <div class="note-box">{_esc(MEDIAN_HELP_PLAIN)}</div>
    <h3>План на следующую сессию</h3>
    <ul class="plan">{plan_items}</ul>
    <h3>Карточки рекомендаций</h3>
    {tips_list or '<p class="muted">Замечаний нет.</p>'}
    <h3>Сводная таблица</h3>
    {_df_table(tips_df)}
    """

    limits_body = ""
    if limit_violations is not None and not limit_violations.empty:
        limits_body += '<p class="warn">Обнаружены превышения лимита:</p>'
        limits_body += _df_table(limit_violations)
    else:
        limits_body += f'<p class="ok">Превышений лимита {instrument_limit} не обнаружено.</p>'
    limits_body += "<h3>Все инструменты</h3>" + _df_table(inst_counts, max_rows=200)

    summary_metrics = _metrics_grid(
        [
            ("Заявок", str(summary.get("total", 0))),
            ("Инструментов", str(summary.get("instruments", 0))),
            ("Объём, лотов", f"{summary.get('total_volume', 0):.0f}"),
            ("Средний объём", f"{summary.get('avg_volume', 0):.2f}"),
            ("Интервал", summary.get("time_min", "—") + " — " + summary.get("time_max", "—")),
        ]
    )

    burst_metrics = _metrics_grid(
        [
            ("Макс. покупок / 100 мс", str(diag.get("max_100ms", 0))),
            ("Макс. покупок / 1 с", str(diag.get("max_1s", 0))),
            ("Критерий №6, лимит/день", str(day_limit_c6)),
            ("Корзина заявок", "Да" if use_basket else "Нет"),
        ]
    )

    body_sections = (
        _section(
            "1. Общая статистика",
            "stats",
            f"""
            <p class="muted">Файл заявок: <b>{_esc(upload_name or "—")}</b>.
            Интервал анализа: <b>{_esc(session_interval or "без фильтра")}</b>.</p>
            {summary_metrics}
            <h3>Распределение по статусам</h3>
            {_df_table(status_breakdown(df))}
            <h3>Базисы поставки</h3>
            {_df_table(basis_distribution(df))}
            """,
        )
        + _section("2. Графики", "charts", charts_html or '<p class="muted">Нет данных для графиков.</p>')
        + _section(
            "3. Проверка критериев",
            "criteria",
            f"""
            {burst_metrics}
            <table class="data-table">
              <thead>
                <tr><th>№</th><th>Критерий</th><th>Результат</th><th>Пояснение</th></tr>
              </thead>
              <tbody>{crit_rows}</tbody>
            </table>
            <h3>Сводная таблица</h3>
            {_df_table(results_to_dataframe(results))}
            """,
        )
        + _section(
            "4. Лимиты по инструментам",
            "limits",
            f"<p>Лимит на один инструмент: <b>{instrument_limit}</b>.</p>{limits_body}",
        )
        + _section("5. Рекомендации", "advice", advice_body)
        + lag_section
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Отчёт СПбМТСБ — {_esc(upload_name or "анализ")}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f4f6f8;
      --card: #ffffff;
      --text: #1f2933;
      --muted: #5d6d7e;
      --accent: #1a5276;
      --line: #d5e0ea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px 48px; }}
    header {{
      background: linear-gradient(135deg, #1a5276, #2874a6);
      color: #fff;
      padding: 28px 24px;
      border-radius: 12px;
      margin-bottom: 20px;
    }}
    header h1 {{ margin: 0 0 8px; font-size: 1.75rem; }}
    header p {{ margin: 4px 0; opacity: 0.95; }}
    nav.toc {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px 20px;
      margin-bottom: 20px;
    }}
    nav.toc a {{
      color: var(--accent);
      text-decoration: none;
      margin-right: 14px;
      display: inline-block;
      margin-bottom: 6px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 20px 22px;
      margin-bottom: 18px;
      page-break-inside: avoid;
    }}
    .card h2 {{
      margin: 0 0 14px;
      color: var(--accent);
      border-bottom: 2px solid #eaf2f8;
      padding-bottom: 8px;
      font-size: 1.25rem;
    }}
    .card h3 {{ margin-top: 18px; font-size: 1.05rem; color: #34495e; }}
    .lead {{ font-size: 1.02rem; }}
    .muted {{ color: var(--muted); font-size: 0.95rem; }}
    .ok {{ color: #1e8449; font-weight: 600; }}
    .warn {{ color: #c0392b; font-weight: 600; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin: 12px 0 4px;
    }}
    .metric {{
      background: #f8fbfd;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .metric-label {{ font-size: 0.82rem; color: var(--muted); }}
    .metric-value {{ font-size: 1.15rem; font-weight: 700; margin-top: 4px; }}
    table.data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      margin-top: 8px;
    }}
    table.data-table th, table.data-table td {{
      border: 1px solid var(--line);
      padding: 7px 9px;
      text-align: left;
      vertical-align: top;
    }}
    table.data-table th {{ background: #eaf2f8; }}
    table.data-table tr:nth-child(even) td {{ background: #fafbfc; }}
    .chart-block {{
      margin: 16px 0;
      padding: 8px 0;
      border-top: 1px dashed var(--line);
    }}
    .chart-block figcaption {{
      font-weight: 600;
      margin-bottom: 6px;
      color: #34495e;
    }}
    .tip {{
      border-left: 4px solid #2874a6;
      background: #f8fbfd;
      padding: 10px 12px;
      margin: 10px 0;
      border-radius: 0 8px 8px 0;
    }}
    .tip-title {{ font-weight: 700; margin-bottom: 4px; }}
    .tag {{
      font-size: 0.78rem;
      background: #eaf2f8;
      color: #1a5276;
      padding: 2px 8px;
      border-radius: 999px;
      margin-left: 6px;
    }}
    ul.plan {{ margin-top: 8px; }}
    .note-box {{
      background: #fef9e7;
      border: 1px solid #f9e79f;
      border-radius: 8px;
      padding: 10px 14px;
      margin: 12px 0;
      font-size: 0.94rem;
      color: #5d4e37;
    }}
    footer {{
      text-align: center;
      color: var(--muted);
      font-size: 0.85rem;
      margin-top: 8px;
    }}
    @media print {{
      body {{ background: #fff; }}
      .wrap {{ max-width: none; padding: 0; }}
      nav.toc {{ display: none; }}
      .card {{ box-shadow: none; page-break-inside: auto; }}
      header {{ border-radius: 0; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Анализатор торгов СПбМТСБ</h1>
      <p>Полный отчёт по всем разделам приложения</p>
      <p>Сформирован: {generated}</p>
    </header>
    <nav class="toc">
      <a href="#stats">1. Статистика</a>
      <a href="#charts">2. Графики</a>
      <a href="#criteria">3. Критерии</a>
      <a href="#limits">4. Лимиты</a>
      <a href="#advice">5. Рекомендации</a>
      <a href="#lag">6. Задержка</a>
    </nav>
    {body_sections}
    <footer>
      Критерии №1–2 проверяются в упрощённом режиме. Для интерактивных графиков нужен доступ к интернету (Plotly CDN).
    </footer>
  </div>
</body>
</html>
"""
