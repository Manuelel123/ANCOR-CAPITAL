"""Reproducible load-time measurement of the main pages (real market data).

Runs the app in-process with Flask's test client (the server on port 5000 is
never touched), a throw-away SQLite database in a temporary directory and
test users created here (novato, inversionista and gestor). Market data
is real (Tiingo when ``TIINGO_API_KEY`` is set, else Yahoo Finance); the
InsForge price store is *not* used, so "cold" means every in-process cache
is empty and the data comes from the providers.

Methodology, per page and repetition:

* cold: every in-memory cache is cleared, then the page is requested once;
* warm: the same request again right away (caches filled by the cold load).

One unrecorded warm-up pass runs first, so imports, template compilation and
the vectorbt/numba JIT are not attributed to the first page. The report keeps
every sample and the median of each series.

Usage::

    uv run python scripts/measure_pages.py --repeat 3 --output scripts/measurements/before.json
    uv run python scripts/measure_pages.py --compare before.json after.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
CSRF_PATTERN = re.compile(rb'name="csrf_token"\s+value="([^"]+)"')
ERROR_FLASH = re.compile(rb'class="flash flash-error"[\s\S]*?class="flash__message">([^<]*)<')
PASSWORD = "medicion-1"
PROFILE_ANSWERS = {"k1": 3, "aporte": 3, "p2": 2, "p1": 3, "p6": 1}
USERS = {
    "novato": "novato@medicion.local",
    "inversionista": "inversionista@medicion.local",
    "gestor": "gestor@medicion.local",
}
PORTFOLIO_WEIGHTS = {"AAPL": 0.4, "MSFT": 0.35, "GLD": 0.25}
PORTFOLIO_DAYS = 365


def clear_caches() -> None:
    """Empty every in-process cache of market data and backtests."""
    from optimizacion_portafolios.data import fx, market_data
    from optimizacion_portafolios.models import hrp_backtest

    try:
        from optimizacion_portafolios.data.cache import clear_all
    except ImportError:  # before data/cache.py existed
        clear_all = None
    if clear_all:
        clear_all()
    market_data.clear_price_cache()
    for name in ("clear_info_cache", "clear_fx_cache"):
        function = getattr(market_data, name, None)
        if function:
            function()
    info = getattr(market_data, "_cached_ticker_info", None)
    if hasattr(info, "cache_clear"):
        info.cache_clear()
    for function in (getattr(fx, "ticker_currency", None), getattr(fx, "_usd_rate_cached", None)):
        if hasattr(function, "cache_clear"):
            function.cache_clear()
    clear = getattr(fx, "clear_cache", None)
    if clear:
        clear()
    hrp_backtest.clear_backtest_cache()


def build_app(instance: Path):
    from dotenv import find_dotenv, load_dotenv

    # Provider keys (TIINGO_API_KEY) from .env; never overrides the environment.
    load_dotenv(find_dotenv(usecwd=True))
    os.environ["ATLAS_INSTANCE_PATH"] = str(instance)
    from optimizacion_portafolios.web import create_app

    app = create_app(
        {
            "TESTING": True,  # no price store, tables created directly, no vectorbt warm-up thread
            "SECRET_KEY": "measurement-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(instance / "measure.db").replace("\\", "/"),
        }
    )
    return app


def create_users(app) -> dict[str, str]:
    from optimizacion_portafolios.profiling import evaluate, load_questionnaire
    from optimizacion_portafolios.storage.profiles import save_profile
    from optimizacion_portafolios.storage.users import create_user
    from optimizacion_portafolios.web.auth.passwords import hash_password

    ids = {}
    password_hash = hash_password(PASSWORD)
    with app.app_context():
        for role, email in USERS.items():
            user = create_user(email, password_hash, display_name=role.title(), role=role, role_confirmed=True)
            save_profile(user.id, evaluate(load_questionnaire(), "5m_30m", "mas_5", PROFILE_ANSWERS))
            ids[role] = str(user.id)
    return ids


def create_portfolio(app, user_id: str) -> int:
    """A tracked portfolio fixed ``PORTFOLIO_DAYS`` ago (real closes of that day)."""
    from optimizacion_portafolios.web.portfolios.services import fix_portfolio

    snapshot = {"model": "hrp", "weights": PORTFOLIO_WEIGHTS, "benchmark": "SPY", "params": {}, "metrics": {}}
    with app.test_request_context():
        portfolio = fix_portfolio(
            user_id,
            snapshot,
            name="Portafolio de medición",
            capital=100_000.0,
            benchmark="SPY",
            commission_bps=10.0,
            today=date.today() - timedelta(days=PORTFOLIO_DAYS),
        )
        return portfolio.id


def client_for(app, user_id: str | None):
    client = app.test_client()
    if user_id:
        with client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True
    return client


def csrf(client) -> str:
    match = CSRF_PATTERN.search(client.get("/hrp").data)
    if match is None:
        raise RuntimeError("No CSRF token in /hrp")
    return match.group(1).decode()


def pages(portfolio_id: int) -> list[dict]:
    """``(name, role, method, path, form)`` of every measured page."""
    from optimizacion_portafolios.data.catalogs import BENCHMARKS, PORTAFOLIOS_MONITOREO, UNIVERSOS_HRP
    from optimizacion_portafolios.web.common.guided import GUIDED_UNIVERSES
    from optimizacion_portafolios.web.hrp.routes import BACKTEST_DEFAULTS

    universe = "Portafolio actual"
    hrp_full = {
        "action": "optimize",
        "universe": universe,
        "tickers": list(UNIVERSOS_HRP[universe].values()),
        "benchmark": next(iter(BENCHMARKS)),
        "start_date": (date.today() - timedelta(days=3 * 365)).isoformat(),
        "min_weight": "0",
        "max_weight": "100",
        "backtest": "1",
        **BACKTEST_DEFAULTS,
    }
    guided = {"action": "optimize", "universe": "Acciones y Bitcoin"}
    # inversionista may exclude assets, so its form posts the checked tickers.
    compare_form = {**guided, "tickers": list(GUIDED_UNIVERSES[guided["universe"]].values()), "max_weight": "100"}
    monitored = next(iter(PORTAFOLIOS_MONITOREO))
    market_form = {
        "action": "analyze",
        "view": "portfolio",
        "universe": monitored,
        "tickers": list(PORTAFOLIOS_MONITOREO[monitored].values()),
    }
    return [
        {"name": "Portada sin sesión", "role": None, "method": "GET", "path": "/"},
        {"name": "Principal con sesión", "role": "novato", "method": "GET", "path": "/"},
        {"name": "HRP GET (novato)", "role": "novato", "method": "GET", "path": "/hrp"},
        {"name": "HRP POST guiado (novato)", "role": "novato", "method": "POST", "path": "/hrp", "form": guided},
        {"name": "HRP POST completo + backtest (gestor)", "role": "gestor", "method": "POST", "path": "/hrp", "form": hrp_full},
        {"name": "Black-Litterman GET (novato)", "role": "novato", "method": "GET", "path": "/black-litterman"},
        {"name": "Black-Litterman POST guiado (novato)", "role": "novato", "method": "POST", "path": "/black-litterman", "form": guided},
        {"name": "Comparación POST (inversionista)", "role": "inversionista", "method": "POST", "path": "/comparar", "form": compare_form},
        {"name": "Mercado GET (gestor)", "role": "gestor", "method": "GET", "path": "/monitoring"},
        {"name": "Mercado POST portafolio (gestor)", "role": "gestor", "method": "POST", "path": "/monitoring", "form": market_form},
        {"name": "Mis portafolios lista (gestor)", "role": "gestor", "method": "GET", "path": "/portafolios/"},
        {"name": "Mis portafolios panel (gestor)", "role": "gestor", "method": "GET", "path": f"/portafolios/{portfolio_id}"},
    ]


def request_once(client, token: str, page: dict) -> tuple[float, int, str | None]:
    started = time.perf_counter()
    if page["method"] == "GET":
        response = client.get(page["path"])
    else:
        response = client.post(page["path"], data={**page.get("form", {}), "csrf_token": token})
    seconds = time.perf_counter() - started
    error = ERROR_FLASH.search(response.data)
    return seconds, response.status_code, error.group(1).decode().strip() if error else None


def measure(repeat: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="atlas-measure-", ignore_cleanup_errors=True) as directory:
        instance = Path(directory)
        app = build_app(instance)
        users = create_users(app)
        portfolio_id = create_portfolio(app, users["gestor"])
        clients = {role: client_for(app, user_id) for role, user_id in users.items()}
        clients[None] = client_for(app, None)
        tokens = {role: csrf(client) for role, client in clients.items() if role}
        tokens[None] = ""
        selected = pages(portfolio_id)

        print("Warm-up pass (not recorded)...", flush=True)
        for page in selected:
            request_once(clients[page["role"]], tokens[page["role"]], page)

        results = {}
        for page in selected:
            cold, warm, statuses, errors = [], [], set(), set()
            for _ in range(repeat):
                clear_caches()
                for series in (cold, warm):
                    seconds, status, error = request_once(clients[page["role"]], tokens[page["role"]], page)
                    series.append(round(seconds, 4))
                    statuses.add(status)
                    if error:
                        errors.add(error)
            results[page["name"]] = {
                "method": page["method"],
                "path": page["path"],
                "role": page["role"],
                "status": sorted(statuses),
                "errors": sorted(errors),
                "cold": cold,
                "warm": warm,
                "cold_median": round(median(cold), 4),
                "warm_median": round(median(warm), 4),
            }
            print(f"{page['name']:<45} cold {median(cold):7.3f} s  warm {median(warm):7.3f} s  {sorted(statuses)} {sorted(errors) or ''}", flush=True)
        from optimizacion_portafolios.storage import db

        with app.app_context():
            db.engine.dispose()  # release the SQLite file so the directory can be removed
    return results


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def compare(before_path: str, after_path: str) -> None:
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))["pages"]
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))["pages"]
    print("| Página | Frío antes (s) | Frío después (s) | Δ frío | Caliente antes (s) | Caliente después (s) | Δ caliente |")
    print("|---|---:|---:|---:|---:|---:|---:|")

    def delta(old, new):
        return f"{(new / old - 1) * 100:+.0f} %" if old else "—"

    for name, old in before.items():
        new = after.get(name)
        if not new:
            continue
        print(
            f"| {name} | {old['cold_median']:.3f} | {new['cold_median']:.3f} | {delta(old['cold_median'], new['cold_median'])} "
            f"| {old['warm_median']:.3f} | {new['warm_median']:.3f} | {delta(old['warm_median'], new['warm_median'])} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=3, help="repetitions per page (default 3)")
    parser.add_argument("--output", help="JSON file for the results")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="print a before/after table")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    sys.path.insert(0, str(ROOT / "src"))
    started = time.perf_counter()
    results = measure(args.repeat)
    report = {
        "meta": {
            "date": date.today().isoformat(),
            "commit": git_commit(),
            "python": platform.python_version(),
            "repeat": args.repeat,
            "provider": "tiingo+yahoo" if os.environ.get("TIINGO_API_KEY") else "yahoo",
            "total_seconds": round(time.perf_counter() - started, 1),
        },
        "pages": results,
    }
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
