"""Mis portafolios: fix, track and manage portfolios (sign-in required)."""

from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for

from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.web.auth.session import current_user_id, login_required
from optimizacion_portafolios.web.common.forms import normalize_ticker, parse_bounded_float
from optimizacion_portafolios.web.decisions import log as decision_log
from optimizacion_portafolios.web.portfolios import alerting, bp, management, services, signals_view
from optimizacion_portafolios.web.portfolios.snapshots import unsign

_NAME_MAX = 120


NAME_MAX_LENGTH = 120


def owned_portfolio(portfolio_id: int):
    """The signed-in user's portfolio, or 404 (also when it belongs to someone else)."""
    portfolio = repositories.get_portfolio(current_user_id(), portfolio_id)
    if portfolio is None:
        abort(404)
    return portfolio


@bp.get("/")
@login_required
def index():
    return render_template(
        "portfolios/index.html", portfolios=services.portfolio_list(current_user_id())
    )


@bp.post("/fijar")
@login_required
def fix():
    """Two steps: ``step=form`` shows the form, ``step=confirm`` stores it."""
    token = request.form.get("snapshot", "")
    snapshot = unsign(token)
    if snapshot is None:
        flash("El resultado expiró o no es válido. Vuelve a calcular el portafolio.", "error")
        return redirect(url_for("portfolios.index"))
    form = services.fix_form(snapshot)
    values = {
        "name": form["default_name"],
        "capital": f"{form['capital']:.0f}",
        "benchmark": form["benchmark"],
        "commission_bps": f"{form['commission_bps']:g}",
    }
    alert_rows = alerting.form_rows()
    if request.form.get("step") == "confirm":
        values = {field: request.form.get(field, values[field]).strip() for field in values}
        alert_rows = alerting.submitted_rows()
        try:
            rules = alerting.parse_rules_form()
            name = " ".join(values["name"].split())[:_NAME_MAX] or form["default_name"]
            capital = parse_bounded_float(
                "capital", values["capital"], 100, 1e12,
                "El capital inicial debe ser numérico.",
                "El capital inicial debe estar entre 100 y 1 000 000 000 000 USD.",
            )
            commission = parse_bounded_float(
                "commission_bps", values["commission_bps"], 0, 500,
                "La comisión debe ser numérica.",
                "La comisión debe estar entre 0 y 500 puntos básicos.",
            )
            benchmark = normalize_ticker(values["benchmark"])
            portfolio = services.fix_portfolio(
                current_user_id(), snapshot, name=name, capital=capital,
                benchmark=benchmark, commission_bps=commission, alert_rules=rules,
            )
        except ValueError as error:
            flash(str(error), "error")
        else:
            decision_log.record(
                snapshot["model"],
                snapshot["weights"],
                {**(snapshot.get("params") or {}), "portfolio_id": portfolio.id, "name": portfolio.name},
                source="fijar",
            )
            flash(f"«{portfolio.name}» quedó fijado como portafolio óptimo. El seguimiento empieza el {portfolio.tracking_start.isoformat()}.", "success")
            return redirect(url_for("portfolios.detail", portfolio_id=portfolio.id))
    return render_template(
        "portfolios/fix.html", form=form, values=values, snapshot=token, alert_rows=alert_rows
    )


@bp.get("/<int:portfolio_id>")
@login_required
def detail(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    return render_template("portfolios/detail.html", portfolio=services.portfolio_detail(portfolio))


@bp.route("/<int:portfolio_id>/alertas", methods=["GET", "POST"])
@login_required
def alerts(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    alerting.ensure_rules(portfolio)
    rows = alerting.form_rows(alerting.current_rules(portfolio))
    if request.method == "POST":
        try:
            repositories.set_alert_rules(portfolio, alerting.parse_rules_form())
        except ValueError as error:
            flash(str(error), "error")
            rows = alerting.submitted_rows()
        else:
            if portfolio.is_active:
                try:
                    alerting.evaluate_portfolio(portfolio, services.analyze(portfolio))
                except ValueError as error:
                    flash(f"Umbrales guardados; no se pudieron reevaluar las alertas: {error}", "warning")
            flash("Umbrales de alerta guardados.", "success")
            return redirect(url_for("portfolios.alerts", portfolio_id=portfolio.id))
    return render_template(
        "portfolios/alerts.html",
        portfolio=services.portfolio_header(portfolio),
        rows=rows,
        status=alerting.status_view(portfolio),
        history=[alerting.event_row(event) for event in repositories.alert_history(portfolio)],
    )


def _active_portfolio(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    if not portfolio.is_active:
        flash("El portafolio está archivado: conserva su historial pero no admite cambios.", "warning")
        return None, redirect(url_for("portfolios.detail", portfolio_id=portfolio.id))
    return portfolio, None


def _percent(field: str, label: str, low: float, high: float, default: str = "") -> float | None:
    raw = request.form.get(field, default).strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{label} debe ser numérico.") from error
    if not low <= value <= high:
        raise ValueError(f"{label} debe estar entre {low:g} % y {high:g} %.")
    return value / 100


def _change_request(portfolio) -> dict:
    """Arguments of ``management.build_change`` from the management form."""
    action = request.form.get("action", "")
    if action == "rebalance":
        return {"action": action, "method": request.form.get("rebalance_method", "targets")}
    if action == "remove":
        return {
            "action": action,
            "method": request.form.get("remove_method", "proportional"),
            "removed": request.form.getlist("remove"),
        }
    if action == "add":
        tickers = [normalize_ticker(t) for t in request.form.get("add_tickers", "").split(",") if t.strip()]
        if not tickers:
            raise ValueError("Escribe el ticker del activo que quieres agregar.")
        method = request.form.get("add_method", "manual")
        weight = _percent("add_weight", "El peso del activo nuevo", 0.01, 99.99)
        if method == "manual" and weight is None:
            raise ValueError("Indica el peso del activo nuevo o elige re-optimizar.")
        if method == "manual" and weight * len(tickers) >= 1:
            raise ValueError("El peso de los activos nuevos debe sumar menos de 100 %.")
        view = _percent("add_view", "La view anual", -100, 1000)
        if portfolio.model == "black_litterman" and method == "reoptimize" and view is None:
            raise ValueError("Indica la view anual del activo nuevo para re-optimizar con Black-Litterman.")
        return {
            "action": action,
            "method": method,
            "added": {t: weight or 0.0 for t in tickers},
            "views": {t: view for t in tickers} if view is not None else {},
        }
    raise ValueError("Elige una acción.")


@bp.get("/<int:portfolio_id>/gestionar")
@login_required
def manage(portfolio_id: int):
    portfolio, redirect_response = _active_portfolio(portfolio_id)
    if redirect_response:
        return redirect_response
    return render_template(
        "portfolios/manage.html",
        portfolio=services.portfolio_header(portfolio),
        holdings=[(h.ticker, h.target_weight) for h in portfolio.current_version.holdings],
        has_bounds=management.bounds_for(portfolio) is not None,
        values=request.args,
    )


@bp.post("/<int:portfolio_id>/gestionar/vista-previa")
@login_required
def preview(portfolio_id: int):
    portfolio, redirect_response = _active_portfolio(portfolio_id)
    if redirect_response:
        return redirect_response
    try:
        change = management.build_change(portfolio, **_change_request(portfolio))
    except ValueError as error:
        flash(str(error), "error")
        return render_template(
            "portfolios/manage.html",
            portfolio=services.portfolio_header(portfolio),
            holdings=[(h.ticker, h.target_weight) for h in portfolio.current_version.holdings],
            has_bounds=management.bounds_for(portfolio) is not None,
            values=request.form,
        )
    return render_template(
        "portfolios/preview.html",
        portfolio=services.portfolio_header(portfolio),
        preview=management.preview_view(change),
        token=management.sign_change(change),
    )


@bp.post("/<int:portfolio_id>/gestionar/confirmar")
@login_required
def confirm(portfolio_id: int):
    portfolio, redirect_response = _active_portfolio(portfolio_id)
    if redirect_response:
        return redirect_response
    try:
        change = management.load_change(request.form.get("token", ""), portfolio)
        note = " ".join(request.form.get("note", "").split())[:500] or None
        version = management.confirm_change(portfolio, change, note)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("portfolios.manage", portfolio_id=portfolio.id))
    flash(
        f"Versión {version.number} guardada: {services.REASON_LABELS.get(version.reason, version.reason).lower()} "
        f"con costo de ${version.cost:,.2f}. El seguimiento continúa sin reiniciarse.",
        "success",
    )
    return redirect(url_for("portfolios.detail", portfolio_id=portfolio.id))


@bp.get("/<int:portfolio_id>/versiones")
@login_required
def versions(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    numbers = [v.number for v in portfolio.versions]
    first = request.args.get("a", type=int)
    second = request.args.get("b", type=int)
    comparison = None
    if first in numbers and second in numbers and first != second:
        comparison = services.compare_versions(portfolio, first, second)
    elif request.args.get("a") or request.args.get("b"):
        flash("Elige dos versiones distintas para comparar.", "warning")
    return render_template(
        "portfolios/versions.html",
        portfolio=services.portfolio_header(portfolio),
        versions=services.versions_view(portfolio),
        comparison=comparison,
        selected=(first or (numbers[-2] if len(numbers) > 1 else None), second or numbers[-1]),
    )


@bp.post("/<int:portfolio_id>/archivar")
@login_required
def archive(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    if portfolio.is_active:
        repositories.archive_portfolio(portfolio, datetime.now(timezone.utc))
        flash("Portafolio archivado: deja de monitorearse y conserva todo su historial.", "info")
    return redirect(url_for("portfolios.detail", portfolio_id=portfolio.id))


@bp.post("/<int:portfolio_id>/renombrar")
@login_required
def rename(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    name = " ".join(request.form.get("name", "").split())
    if not 1 <= len(name) <= NAME_MAX_LENGTH:
        flash(f"El nombre debe tener entre 1 y {NAME_MAX_LENGTH} caracteres.", "error")
    else:
        repositories.rename_portfolio(portfolio, name)
        flash(f"El portafolio ahora se llama «{name}».", "success")
    return redirect(url_for("portfolios.index"))


@bp.post("/<int:portfolio_id>/visibilidad")
@login_required
def visibility(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    visible = request.form.get("visible") == "1"
    repositories.set_portfolio_visibility(portfolio, visible)
    flash(f"«{portfolio.name}» {'ya se muestra' if visible else 'ya no se muestra'} a los usuarios.", "info")
    return redirect(url_for("portfolios.index"))


@bp.post("/<int:portfolio_id>/eliminar")
@login_required
def delete(portfolio_id: int):
    """Permanent: the name must be typed, because nothing brings it back."""
    portfolio = owned_portfolio(portfolio_id)
    name = portfolio.name
    if request.form.get("confirmacion", "").strip() != name:
        flash("Escribe el nombre exacto del portafolio para eliminarlo.", "error")
        return redirect(url_for("portfolios.index"))
    repositories.delete_portfolio(portfolio)
    flash(f"«{name}» se eliminó con todo su historial.", "info")
    return redirect(url_for("portfolios.index"))


@bp.get("/<int:portfolio_id>/senales")
@login_required
def signals(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    values = signals_view.form_values()
    result = None
    try:
        config = signals_view.config_from(values, portfolio.commission_bps)
        result = signals_view.signals_view(portfolio, config)
    except ValueError as error:
        flash(str(error), "error")
    return render_template(
        "portfolios/signals.html",
        portfolio=services.portfolio_header(portfolio),
        values=values,
        result=result,
    )
