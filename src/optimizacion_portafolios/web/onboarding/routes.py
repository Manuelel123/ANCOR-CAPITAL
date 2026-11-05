"""Data notice (screen 0) and introduction.

The notice has one required checkbox, stored with its version. The
introduction opens by itself once, right after the notice, and stays in the
user menu.
"""

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


@bp.get("/introduccion")
def introduction():
    if current_user.intro_vista_at is None:
        current_user.intro_vista_at = datetime.now(timezone.utc)
        db.session.commit()
    return render_template(
        "onboarding/introduction.html", next_step=pending_profile_step(current_user) or "main.index"
    )


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
