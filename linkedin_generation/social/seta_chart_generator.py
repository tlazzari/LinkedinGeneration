"""Animated GIF chart generator for Seta Capital — Market Intelligence pillar.

Fetches live data from ECB (EUR/CNY, EUR/USD), FRED (10Y yield, CPI),
and World Bank (GDP growth). Renders an animated 1080×1080 GIF with
Seta Capital branding (navy #1B2A4A + gold #C4A35A).

Chart rotates through 3 types on each call:
  0 — EUR/CNY 90-day line chart (Europe-China FX focus)
  1 — GDP growth bar chart: China vs major EU economies (last 3 years)
  2 — EUR/CNY + FRED 10Y yield dual-axis line chart (FX + financing cost)

Returns: (gif_path: Path, data_summary: str) — summary is fed to the LLM.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.figure import Figure
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Branding ────────────────────────────────────────────────────────────────
SETA_NAVY  = "#1B2A4A"
SETA_GOLD  = "#C4A35A"
SETA_WHITE = "#F0EDE8"
SETA_GREY  = "#6B7A8D"

# Chart type rotation — stored externally via index 0/1/2
CHART_TYPES = ["fx_trend", "gdp_bars", "dual_axis"]

# ── Data structures ──────────────────────────────────────────────────────────
@dataclass
class SeriesData:
    label: str
    dates: list[str]
    values: list[float]
    unit: str = ""


# ── ECB data fetcher ─────────────────────────────────────────────────────────
def _fetch_ecb_series(series_key: str, obs: int = 90) -> Optional[SeriesData]:
    """Fetch a daily ECB exchange rate series (CSV format)."""
    import urllib.request
    url = (
        f"https://data-api.ecb.europa.eu/service/data/EXR/{series_key}"
        f"?lastNObservations={obs}&format=csvdata"
    )
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/csv"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        dates, values = [], []
        for row in reader:
            date_val = row.get("TIME_PERIOD", "")
            obs_val  = row.get("OBS_VALUE", "")
            if date_val and obs_val:
                try:
                    values.append(float(obs_val))
                    dates.append(date_val)
                except ValueError:
                    pass
        if len(values) < 10:
            logger.warning("ECB series %s returned only %d points", series_key, len(values))
            return None
        label_map = {
            "D.CNY.EUR.SP00.A": "EUR/CNY",
            "D.USD.EUR.SP00.A": "EUR/USD",
        }
        return SeriesData(
            label=label_map.get(series_key, series_key),
            dates=dates,
            values=values,
            unit="",
        )
    except Exception as exc:
        logger.warning("ECB fetch failed for %s: %s", series_key, exc)
        return None


# ── FRED data fetcher ────────────────────────────────────────────────────────
def _fetch_fred_series(series_id: str, obs: int = 90) -> Optional[SeriesData]:
    import urllib.request
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.warning("FRED_API_KEY not set, skipping FRED fetch")
        return None
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
        f"&limit={obs}&sort_order=asc&observation_start="
        + (datetime.utcnow() - timedelta(days=obs * 2)).strftime("%Y-%m-%d")
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        obs_list = data.get("observations", [])
        dates, values = [], []
        for o in obs_list:
            if o.get("value", ".") != ".":
                try:
                    values.append(float(o["value"]))
                    dates.append(o["date"])
                except ValueError:
                    pass
        dates = dates[-obs:]
        values = values[-obs:]
        if len(values) < 5:
            return None
        label_map = {
            "DGS10": "US 10Y Yield (%)",
            "FEDFUNDS": "Fed Funds Rate (%)",
            "CPIAUCSL": "US CPI",
        }
        return SeriesData(
            label=label_map.get(series_id, series_id),
            dates=dates,
            values=values,
            unit="%",
        )
    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return None


# ── World Bank GDP fetcher ───────────────────────────────────────────────────
def _fetch_world_bank_gdp() -> Optional[list[dict]]:
    """Fetch latest GDP growth for CN, DE, IT, FR, US. Returns list of {country, year, value}."""
    import urllib.request
    countries = "CN;DE;IT;FR;US"
    url = (
        f"https://api.worldbank.org/v2/country/{countries}"
        f"/indicator/NY.GDP.MKTP.KD.ZG?format=json&mrv=4&per_page=100"
    )
    labels = {"CN": "China", "DE": "Germany", "IT": "Italy", "FR": "France", "US": "USA"}
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        if len(data) < 2:
            return None
        results = []
        for entry in data[1]:
            val = entry.get("value")
            iso = entry.get("countryiso3code", "")[:2]
            year = entry.get("date", "")
            if val is not None and iso in labels:
                results.append({
                    "country": labels[iso],
                    "iso": iso,
                    "year": int(year) if year.isdigit() else 0,
                    "value": round(float(val), 2),
                })
        return results if results else None
    except Exception as exc:
        logger.warning("World Bank GDP fetch failed: %s", exc)
        return None


# ── Chart renderer helpers ───────────────────────────────────────────────────
def _apply_seta_style(fig: Figure, ax) -> None:
    fig.patch.set_facecolor(SETA_NAVY)
    ax.set_facecolor(SETA_NAVY)
    ax.tick_params(colors=SETA_WHITE, labelsize=14)
    ax.xaxis.label.set_color(SETA_WHITE)
    ax.yaxis.label.set_color(SETA_WHITE)
    ax.title.set_color(SETA_GOLD)
    for spine in ax.spines.values():
        spine.set_edgecolor(SETA_GREY)
    ax.grid(True, color=SETA_GREY, alpha=0.25, linewidth=0.7)


def _add_branding(fig: Figure, subtitle: str = "") -> None:
    fig.text(0.5, 0.01, "SETA CAPITAL  •  Market Intelligence",
             ha="center", va="bottom", fontsize=13,
             color=SETA_GOLD, fontweight="bold")
    if subtitle:
        fig.text(0.5, 0.96, subtitle,
                 ha="center", va="top", fontsize=12, color=SETA_GREY)


def _thin_date_labels(dates: list[str], n_ticks: int = 6) -> tuple[list[int], list[str]]:
    step = max(1, len(dates) // n_ticks)
    idxs = list(range(0, len(dates), step))
    # Always include last
    if idxs[-1] != len(dates) - 1:
        idxs.append(len(dates) - 1)
    labels = [dates[i][5:] for i in idxs]  # MM-DD
    return idxs, labels


def _frames_to_gif(frames: list[Image.Image], path: Path, frame_ms: int = 120) -> None:
    frames[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=frame_ms,
        loop=0,
        optimize=False,
    )


def _fig_to_pil(fig: Figure) -> Image.Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor())
    buf.seek(0)
    img = Image.open(buf).copy().convert("RGB")
    buf.close()
    return img


# ── Chart type 0: EUR/CNY 90-day line draw-on animation ─────────────────────
def _chart_fx_trend(
    eurchny: SeriesData,
    eurusd: Optional[SeriesData],
    n_frames: int = 25,
) -> list[Image.Image]:
    dates  = eurchny.dates
    values = eurchny.values
    x      = np.arange(len(dates))
    y_min  = min(values) * 0.998
    y_max  = max(values) * 1.002
    tick_idxs, tick_labels = _thin_date_labels(dates)

    frames: list[Image.Image] = []
    for f in range(1, n_frames + 1):
        end = max(2, int(len(x) * f / n_frames))
        fig, ax = plt.subplots(figsize=(10.8, 10.8))
        _apply_seta_style(fig, ax)

        ax.plot(x[:end], values[:end], color=SETA_GOLD, linewidth=2.5, solid_capstyle="round")
        ax.fill_between(x[:end], values[:end], y_min, alpha=0.18, color=SETA_GOLD)

        # Latest value annotation at tip
        ax.annotate(
            f"{values[end-1]:.4f}",
            xy=(x[end-1], values[end-1]),
            xytext=(6, 6), textcoords="offset points",
            color=SETA_GOLD, fontsize=14, fontweight="bold",
        )

        ax.set_xlim(0, len(x) - 1)
        ax.set_ylim(y_min, y_max)
        ax.set_xticks(tick_idxs)
        ax.set_xticklabels(tick_labels, rotation=30, ha="right")
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.4f"))
        ax.set_title("EUR / CNY  —  90 Day Trend", fontsize=20, pad=14, fontweight="bold")

        # Change % in corner
        pct = (values[-1] - values[0]) / values[0] * 100
        direction = "▲" if pct > 0 else "▼"
        color = "#E05C5C" if pct > 0 else "#5CE05C"  # red=CNY weakening, green=strengthening
        ax.text(0.98, 0.06, f"{direction} {abs(pct):.2f}%  (90d)",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=15, color=color, fontweight="bold")

        fig.tight_layout(rect=[0, 0.04, 1, 0.96])
        _add_branding(fig, subtitle="EUR/CNY spot rate  |  Source: ECB")
        frames.append(_fig_to_pil(fig))
        plt.close(fig)
    return frames


# ── Chart type 1: GDP growth bar chart ──────────────────────────────────────
def _chart_gdp_bars(gdp_rows: list[dict], n_frames: int = 25) -> list[Image.Image]:
    # Pick the two most recent complete years per country
    from collections import defaultdict
    by_country: dict[str, list] = defaultdict(list)
    for r in gdp_rows:
        by_country[r["country"]].append(r)
    for k in by_country:
        by_country[k].sort(key=lambda r: r["year"], reverse=True)

    order = ["China", "Germany", "Italy", "France", "USA"]
    countries = [c for c in order if c in by_country]
    latest_year = max(r["year"] for r in gdp_rows if r["value"] is not None)
    prev_year = latest_year - 1

    vals_latest = [by_country[c][0]["value"] if by_country[c] else 0 for c in countries]
    vals_prev   = [next((r["value"] for r in by_country[c] if r["year"] == prev_year), 0)
                   for c in countries]

    x = np.arange(len(countries))
    w = 0.36
    frames: list[Image.Image] = []

    for f in range(1, n_frames + 1):
        scale = f / n_frames
        fig, ax = plt.subplots(figsize=(10.8, 10.8))
        _apply_seta_style(fig, ax)

        bars_prev = ax.bar(x - w/2, [v * scale for v in vals_prev], w,
                           color=SETA_GREY, alpha=0.75, label=str(prev_year))
        bars_curr = ax.bar(x + w/2, [v * scale for v in vals_latest], w,
                           color=SETA_GOLD, label=str(latest_year))

        if f == n_frames:
            for bar, val in zip(bars_curr, vals_latest):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                        f"{val:+.1f}%", ha="center", va="bottom",
                        color=SETA_GOLD, fontsize=13, fontweight="bold")

        ax.set_xticks(x)
        ax.set_xticklabels(countries, fontsize=15)
        ax.set_ylabel("GDP Growth (%)", color=SETA_WHITE, fontsize=14)
        ax.set_title("GDP Growth — Europe & China", fontsize=20, pad=14, fontweight="bold")
        ax.axhline(0, color=SETA_WHITE, linewidth=0.8, alpha=0.4)
        ax.legend(facecolor=SETA_NAVY, edgecolor=SETA_GREY,
                  labelcolor=SETA_WHITE, fontsize=13)

        fig.tight_layout(rect=[0, 0.04, 1, 0.96])
        _add_branding(fig, subtitle="Annual GDP growth rate (%)  |  Source: World Bank")
        frames.append(_fig_to_pil(fig))
        plt.close(fig)
    return frames


# ── Chart type 2: EUR/CNY + US 10Y dual-axis line chart ─────────────────────
def _chart_dual_axis(eurchny: SeriesData, yield10y: SeriesData, n_frames: int = 25) -> list[Image.Image]:
    # Align dates (use shorter series as reference)
    common_n = min(len(eurchny.values), len(yield10y.values))
    fx_vals  = eurchny.values[-common_n:]
    fx_dates = eurchny.dates[-common_n:]
    yr_vals  = yield10y.values[-common_n:]
    x = np.arange(common_n)
    tick_idxs, tick_labels = _thin_date_labels(fx_dates)

    frames: list[Image.Image] = []
    for f in range(1, n_frames + 1):
        end = max(2, int(common_n * f / n_frames))
        fig, ax1 = plt.subplots(figsize=(10.8, 10.8))
        ax2 = ax1.twinx()
        _apply_seta_style(fig, ax1)
        ax2.set_facecolor(SETA_NAVY)
        ax2.tick_params(colors=SETA_WHITE, labelsize=13)
        for spine in ax2.spines.values():
            spine.set_edgecolor(SETA_GREY)

        ax1.plot(x[:end], fx_vals[:end], color=SETA_GOLD, linewidth=2.5, label="EUR/CNY (L)")
        ax2.plot(x[:end], yr_vals[:end], color="#5CE0C8", linewidth=2.0,
                 linestyle="--", label="US 10Y Yield % (R)")

        ax1.set_xlim(0, common_n - 1)
        ax1.set_xticks(tick_idxs)
        ax1.set_xticklabels(tick_labels, rotation=30, ha="right")
        ax1.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.4f"))
        ax1.set_ylabel("EUR/CNY", color=SETA_GOLD, fontsize=14)
        ax2.set_ylabel("US 10Y Yield (%)", color="#5CE0C8", fontsize=14)
        ax2.yaxis.label.set_color("#5CE0C8")

        ax1.set_title("EUR/CNY vs US 10Y Yield", fontsize=20, pad=14, fontweight="bold")

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   facecolor=SETA_NAVY, edgecolor=SETA_GREY,
                   labelcolor=SETA_WHITE, fontsize=12, loc="upper left")

        fig.tight_layout(rect=[0, 0.04, 1, 0.96])
        _add_branding(fig, subtitle="FX rate vs deal financing cost  |  ECB + FRED")
        frames.append(_fig_to_pil(fig))
        plt.close(fig)
    return frames


# ── Public API ───────────────────────────────────────────────────────────────
def generate_market_chart(
    *,
    target_dir: Path,
    chart_type_index: int = 0,
    n_frames: int = 25,
    frame_ms: int = 120,
) -> tuple[Optional[Path], str]:
    """Generate an animated market chart GIF.

    Returns (gif_path, data_summary) where data_summary is a text block
    describing the data shown, suitable for injection into the LLM prompt.
    Returns (None, error_message) on failure.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    chart_type = CHART_TYPES[chart_type_index % len(CHART_TYPES)]
    logger.info("Generating market chart: type=%s", chart_type)

    # Fetch data
    eurchny = _fetch_ecb_series("D.CNY.EUR.SP00.A", obs=90)
    eurusd  = _fetch_ecb_series("D.USD.EUR.SP00.A", obs=90)
    gdp     = _fetch_world_bank_gdp()
    yield10y = _fetch_fred_series("DGS10", obs=90)

    data_summary = _build_summary(eurchny, eurusd, gdp, yield10y)

    frames: Optional[list] = None

    if chart_type == "fx_trend" and eurchny:
        frames = _chart_fx_trend(eurchny, eurusd, n_frames=n_frames)
    elif chart_type == "gdp_bars" and gdp:
        frames = _chart_gdp_bars(gdp, n_frames=n_frames)
    elif chart_type == "dual_axis" and eurchny and yield10y:
        frames = _chart_dual_axis(eurchny, yield10y, n_frames=n_frames)

    # Fallback: if primary chart type's data unavailable, try fx_trend
    if frames is None and eurchny:
        logger.warning("Primary chart type %s failed, falling back to fx_trend", chart_type)
        frames = _chart_fx_trend(eurchny, eurusd, n_frames=n_frames)

    if not frames:
        return None, f"All data sources unavailable: {data_summary}"

    gif_path = target_dir / f"market_chart_{chart_type}_{int(time.time())}.gif"
    _frames_to_gif(frames, gif_path, frame_ms=frame_ms)
    logger.info("Chart GIF saved: %s (%d KB)", gif_path, gif_path.stat().st_size // 1024)
    return gif_path, data_summary


def _build_summary(
    eurchny: Optional[SeriesData],
    eurusd: Optional[SeriesData],
    gdp: Optional[list],
    yield10y: Optional[SeriesData],
) -> str:
    lines = ["LIVE MARKET DATA (use these exact figures in your post):"]

    if eurchny and eurchny.values:
        pct = (eurchny.values[-1] - eurchny.values[0]) / eurchny.values[0] * 100
        direction = "CNY weakening vs EUR" if pct > 0 else "CNY strengthening vs EUR"
        lines.append(
            f"EUR/CNY: {eurchny.values[-1]:.4f}  "
            f"(90d ago: {eurchny.values[0]:.4f},  {pct:+.2f}%,  {direction})"
        )

    if eurusd and eurusd.values:
        pct = (eurusd.values[-1] - eurusd.values[0]) / eurusd.values[0] * 100
        lines.append(
            f"EUR/USD: {eurusd.values[-1]:.4f}  "
            f"(90d ago: {eurusd.values[0]:.4f},  {pct:+.2f}%)"
        )

    if yield10y and yield10y.values:
        lines.append(
            f"US 10Y Treasury Yield: {yield10y.values[-1]:.2f}%  "
            f"(90d ago: {yield10y.values[0]:.2f}%)"
        )

    if gdp:
        from collections import defaultdict
        by_country: dict = defaultdict(list)
        for r in gdp:
            by_country[r["country"]].append(r)
        latest: list[str] = []
        for country in ["China", "Germany", "Italy", "France", "USA"]:
            rows = sorted(by_country.get(country, []), key=lambda r: r["year"], reverse=True)
            if rows:
                latest.append(f"{country} {rows[0]['year']}: {rows[0]['value']:+.1f}%")
        if latest:
            lines.append("GDP growth: " + "  |  ".join(latest))

    if len(lines) == 1:
        return "No market data available."
    return "\n".join(lines)
