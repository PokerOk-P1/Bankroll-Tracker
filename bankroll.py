#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bankroll.py — утилита для учёта и анализа покерных сессий PokerOK / GGPoker.
Описание:
    Скрипт агрегирует результаты сессий из CSV/TSV/текстовых файлов,
    рассчитывает метрики (прибыль, ROI, bb/100, EV, дисперсию),
    строит консольный отчёт и (опционально) график прибыли.
"""

import csv
import argparse
import sys
import math
import statistics
import datetime as dt
from pathlib import Path
from collections import defaultdict, namedtuple
import matplotlib.pyplot as plt


# ------------------------------
# Вспомогательные функции
# ------------------------------

def parse_args():
    """Парсер аргументов командной строки."""
    p = argparse.ArgumentParser(
        description="Утилита анализа банкролла для PokerOK / GGPoker"
    )
    p.add_argument("--data", required=True, nargs="+", help="Пути к CSV/TSV/текстовым файлам сессий")
    p.add_argument("--delimiter", default=",", help="Разделитель в CSV (по умолчанию ,)")
    p.add_argument("--site", default=None, help="Фильтр по названию сайта (например GGPoker)")
    p.add_argument("--from", dest="date_from", default=None, help="Дата начала периода (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", default=None, help="Дата окончания периода (YYYY-MM-DD)")
    p.add_argument("--game", default="all", choices=["cash", "sng", "mtt", "all"], help="Фильтр по типу игры")
    p.add_argument("--currency", default="USD", help="Базовая валюта отчёта (по умолчанию USD)")
    p.add_argument("--fx", default="", help="Курсы валют в виде 'EUR:1.08,USD:1'")
    p.add_argument("--rebuys-are-counts", action="store_true", help="Трактовать rebuys/addons как количество")
    p.add_argument("--plot", default=None, help="Путь к PNG-графику (если указано — построить)")
    p.add_argument("--group-by", default=None, choices=["month", "week", "day", "format", "game"],
                   help="Агрегировать по группе")
    p.add_argument("--export", default=None, help="Экспорт сводки в CSV")
    p.add_argument("--quiet", action="store_true", help="Минимальный вывод")
    p.add_argument("--no-color", action="store_true", help="Отключить цветной вывод")
    p.add_argument("--verbose", action="store_true", help="Подробный лог")
    return p.parse_args()


def colorize(text, color, enable=True):
    """Цветной вывод в терминал (если не отключен)."""
    if not enable:
        return text
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "reset": "\033[0m"
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def parse_fx(fx_str):
    """Парсер строк курсов валют в словарь."""
    fx = {}
    if not fx_str:
        return fx
    pairs = fx_str.split(",")
    for pair in pairs:
        if ":" not in pair:
            continue
        c, r = pair.split(":")
        try:
            fx[c.strip().upper()] = float(r)
        except ValueError:
            pass
    return fx


def safe_float(x):
    """Безопасное преобразование строки в float."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_date(s):
    """Парсинг даты в объект datetime.date."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def read_data(paths, delimiter, verbose=False):
    """Читает все файлы, возвращает список строк (dict)."""
    rows = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"WARN: файл {p} не найден", file=sys.stderr)
            continue
        with open(p, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                row["__source"] = p.name
                rows.append(row)
    if verbose:
        print(f"Загружено строк: {len(rows)} из {len(paths)} файлов")
    return rows


def normalize_row(row, args, fx_map):
    """Приведение типов и вычисление derived-полей."""
    currency = row.get("currency", args.currency).upper()
    fx_rate = fx_map.get(currency, 1.0)

    date = parse_date(row.get("date"))
    if not date:
        return None  # некорректная дата

    # базовые числовые поля
    buyin = safe_float(row.get("buyin")) or 0.0
    cashout = safe_float(row.get("cashout")) or 0.0
    rake = safe_float(row.get("rake")) or 0.0
    stake_bb = safe_float(row.get("stake_bb")) or None
    hands = safe_float(row.get("hands")) or 0
    ev_allin_diff = safe_float(row.get("ev_allin_diff")) or 0.0

    # обработка ребаев/аддонов
    rebuys = safe_float(row.get("rebuys")) or 0.0
    addons = safe_float(row.get("addons")) or 0.0
    if args.rebuys_are_counts:
        rebuys = rebuys * buyin
        addons = addons * buyin

    total_buyin = buyin + rebuys + addons
    result = cashout - total_buyin - rake
    ev_result = result + ev_allin_diff

    # перевод в базовую валюту
    for k in ["buyin", "cashout", "rake", "rebuys", "addons", "total_buyin", "result", "ev_result", "ev_allin_diff"]:
        locals()[k] *= fx_rate

    # возвращаем словарь нормализованных данных
    return {
        "date": date,
        "site": row.get("site", "").strip(),
        "game": row.get("game", "").lower(),
        "format": row.get("format", "").strip(),
        "buyin": buyin * fx_rate,
        "cashout": cashout * fx_rate,
        "rake": rake * fx_rate,
        "rebuys": rebuys * fx_rate,
        "addons": addons * fx_rate,
        "total_buyin": total_buyin * fx_rate,
        "result": result * fx_rate,
        "ev_result": ev_result * fx_rate,
        "ev_allin_diff": ev_allin_diff * fx_rate,
        "stake_bb": stake_bb,
        "hands": hands,
        "currency": args.currency
    }


def filter_rows(rows, args):
    """Фильтрация по дате, сайту, типу игры."""
    res = []
    date_from = parse_date(args.date_from) if args.date_from else None
    date_to = parse_date(args.date_to) if args.date_to else None

    for r in rows:
        if args.site and r["site"].lower() != args.site.lower():
            continue
        if args.game != "all" and r["game"] != args.game:
            continue
        if date_from and r["date"] < date_from:
            continue
        if date_to and r["date"] > date_to:
            continue
        res.append(r)
    return res


def aggregate(rows, group_by):
    """Агрегирование по указанному признаку."""
    if not group_by:
        return {"ALL": rows}

    grouped = defaultdict(list)
    for r in rows:
        if group_by == "month":
            key = r["date"].strftime("%Y-%m")
        elif group_by == "week":
            key = f"{r['date'].isocalendar()[0]}-W{r['date'].isocalendar()[1]}"
        elif group_by == "day":
            key = r["date"].isoformat()
        elif group_by == "format":
            key = r["format"]
        elif group_by == "game":
            key = r["game"]
        else:
            key = "ALL"
        grouped[key].append(r)
    return grouped


def compute_stats(rows):
    """Рассчитывает ключевые метрики по группе строк."""
    n = len(rows)
    if n == 0:
        return None

    result_sum = sum(r["result"] for r in rows)
    ev_sum = sum(r["ev_result"] for r in rows)
    hands_sum = sum(r["hands"] for r in rows)
    profit_list = [r["result"] for r in rows]
    buyin_sum = sum(r["total_buyin"] for r in rows)
    itm_count = sum(1 for r in rows if r["cashout"] > 0)
    stake_rows = [r for r in rows if r["stake_bb"] and r["hands"] > 0]
    bb_per_100 = None
    if stake_rows:
        bb_sum = sum((r["result"] / r["stake_bb"]) / (r["hands"] / 100) for r in stake_rows)
        bb_per_100 = bb_sum / len(stake_rows)
    roi_pct = (result_sum / buyin_sum * 100) if buyin_sum > 0 else None
    itm_pct = itm_count / n * 100 if n > 0 else None
    stddev = statistics.stdev(profit_list) if len(profit_list) > 1 else 0.0

    return {
        "sessions": n,
        "profit": result_sum,
        "ev_profit": ev_sum,
        "roi_pct": roi_pct,
        "itm_pct": itm_pct,
        "bb_per_100": bb_per_100,
        "stddev": stddev,
        "hands": hands_sum,
    }


def print_dashboard(rows, grouped, args):
    """Печатает итоговый отчёт."""
    enable_color = not args.no_color

    total_stats = compute_stats(rows)
    if not total_stats:
        print("Нет данных для отчёта.")
        return

    profit_text = colorize(f"{total_stats['profit']:+.2f} {args.currency}",
                           "green" if total_stats["profit"] >= 0 else "red", enable_color)

    print("=" * 70)
    print(f"Bankroll Report (site={args.site or 'All'} | currency={args.currency})")
    print(f"Total sessions: {total_stats['sessions']}")
    print(f"Total profit:  {profit_text}")
    if total_stats["roi_pct"] is not None:
        print(f"ROI:           {total_stats['roi_pct']:.2f}%")
    if total_stats["itm_pct"] is not None:
        print(f"ITM:           {total_stats['itm_pct']:.1f}%")
    if total_stats["bb_per_100"] is not None:
        print(f"bb/100:        {total_stats['bb_per_100']:+.2f}")
    print("=" * 70)

    if args.group_by:
        print(f"\nBy {args.group_by}:")
        print(f"{'Group':<15} {'Sessions':>8} {'Profit':>12} {'ROI%':>8} {'ITM%':>8} {'bb/100':>8}")
        for key, group_rows in grouped.items():
            s = compute_stats(group_rows)
            if not s:
                continue
            pr = colorize(f"{s['profit']:+.2f}", "green" if s["profit"] >= 0 else "red", enable_color)
            print(f"{key:<15} {s['sessions']:>8} {pr:>12} "
                  f"{(s['roi_pct'] or 0):>8.1f} {(s['itm_pct'] or 0):>8.1f} {(s['bb_per_100'] or 0):>8.2f}")


def plot_cumulative(rows, path):
    """Строит график кумулятивной прибыли."""
    rows_sorted = sorted(rows, key=lambda r: r["date"])
    dates = [r["date"] for r in rows_sorted]
    cum_profit, cum_ev = [], []
    total = 0.0
    total_ev = 0.0
    for r in rows_sorted:
        total += r["result"]
        total_ev += r["ev_result"]
        cum_profit.append(total)
        cum_ev.append(total_ev)

    plt.figure(figsize=(10, 6))
    plt.plot(dates, cum_profit, label="Profit")
    if any(r["ev_allin_diff"] != 0 for r in rows_sorted):
        plt.plot(dates, cum_ev, "--", label="EV (all-in)")
    plt.title("Bankroll Progress")
    plt.xlabel("Date")
    plt.ylabel(f"Profit ({rows[0]['currency']})")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(path)
    print(f"График сохранён: {path}")


def export_summary(grouped, path):
    """Экспортирует сводку по группам в CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "sessions", "profit", "roi_pct", "itm_pct", "bb_per_100", "hands"])
        for key, rows in grouped.items():
            s = compute_stats(rows)
            if not s:
                continue
            writer.writerow([
                key, s["sessions"], round(s["profit"], 2),
                round(s["roi_pct"] or 0, 2),
                round(s["itm_pct"] or 0, 1),
                round(s["bb_per_100"] or 0, 2),
                s["hands"]
            ])
    print(f"Сводка экспортирована в: {path}")


# ------------------------------
# Основной поток
# ------------------------------

def main():
    args = parse_args()
    fx_map = parse_fx(args.fx)

    raw_rows = read_data(args.data, args.delimiter, args.verbose)

    norm_rows = []
    for r in raw_rows:
        nr = normalize_row(r, args, fx_map)
        if nr:
            norm_rows.append(nr)

    if not norm_rows:
        print("Нет корректных строк для анализа.", file=sys.stderr)
        sys.exit(1)

    filtered = filter_rows(norm_rows, args)
    grouped = aggregate(filtered, args.group_by)

    if not args.quiet:
        print_dashboard(filtered, grouped, args)

    if args.plot:
        plot_cumulative(filtered, args.plot)

    if args.export:
        export_summary(grouped, args.export)


if __name__ == "__main__":
    main()