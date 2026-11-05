"""Data notice (screen 0): one required checkbox, stored with the notice version."""

from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.web.onboarding import bp
from optimizacion_portafolios.web.profile.access import get_data_notice, needs_data_notice, pending_profile_step

CHECKBOX_MESSAGE = "Para continuar, marca la casilla de autorización."


def _next_step():
    step = pending_profile_step(current_user)
    return redirect(url_for(step or "main.index"))


@bp.route("/aviso-datos", methods=["GET", "POST"])
def data_notice():
    if not needs_data_notice(current_user):
        return _next_step()
    notice = get_data_notice()
    if request.method == "POST":
        if request.form.get("acepto") == "1":
            current_user.datos_aceptados_at = datetime.now(timezone.utc)
            current_user.datos_aviso_version = notice.version
            db.session.commit()
            return _next_step()
        flash(CHECKBOX_MESSAGE, "error")
    return render_template("onboarding/data_notice.html", notice=notice)
