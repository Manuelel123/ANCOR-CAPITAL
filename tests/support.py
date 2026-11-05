"""Shared fixtures for the test suite (synthetic prices, local users, Flask client with CSRF)."""

import re
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from optimizacion_portafolios.profiling import evaluate, load_questionnaire
from optimizacion_portafolios.profiling.avisos import load_notices
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.profiles import get_current_profile, save_profile
from optimizacion_portafolios.storage.users import create_user, get_user_by_email
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth.passwords import hash_password

CSRF_PATTERN = re.compile(rb'name="csrf_token"\s+value="([^"]+)"')
TEST_PASSWORD = "secreto1"
# argon2 is slow on purpose: hash the shared test password once.
_TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


def make_prices(tickers: tuple[str, ...], rows: int = 280) -> pd.DataFrame:
    """Deterministic geometric random-walk prices on business days."""
    seed = sum(ord(character) for ticker in tickers for character in ticker)
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(0.00035, 0.012, size=(rows, len(tickers)))
    prices = 100 * np.cumprod(1 + daily_returns, axis=0)
    index = pd.bdate_range("2024-01-02", periods=rows, name="Fecha")
    return pd.DataFrame(prices, index=index, columns=tickers)


def create_test_user(
    app,
    email: str = "ana@example.com",
    password: str = TEST_PASSWORD,
    *,
    role: str = "novato",
    role_confirmed: bool | None = None,
    display_name: str | None = "Ana",
    profile: bool = True,
    verified: bool = True,
    notice: bool = True,
) -> str:
    """Add a local user straight to the database; return ``str(user.id)``.

    By default the user has already been profiled: a current risk profile
    (``TEST_PROFILE_ANSWERS``) and a confirmed role, so the tools are open.
    Pass ``profile=False`` for a brand-new user (no profile, unconfirmed role)
    ``verified=False`` for an account that has not proved its email yet and
    ``notice=False`` for one that has not accepted the data notice.
    """
    if role_confirmed is None:
        role_confirmed = profile
    password_hash = _TEST_PASSWORD_HASH if password == TEST_PASSWORD else hash_password(password)
    with app.app_context():
        user = get_user_by_email(email)
        if user is None:
            user = create_user(
                email, password_hash, display_name=display_name, role=role, role_confirmed=role_confirmed
            )
            if verified:
                user.email_verified_at = datetime.now(timezone.utc)
            if notice:
                user.datos_aceptados_at = datetime.now(timezone.utc)
                user.datos_aviso_version = load_notices().version
            db.session.commit()
        if profile and get_current_profile(user.id) is None:
            add_test_profile(app, user.id)
        return str(user.id)


# Complete answers of a "moderado" profile (limited by capacity), horizon over 3 years.
TEST_PROFILE_CAPITAL = "5m_20m"
TEST_PROFILE_HORIZON = "mas_3"
TEST_PROFILE_ANSWERS = {
    "p1": 3, "p2": 2, "p3": 3, "p4": 3, "p5": 2,
    "p6": 2, "p7": 2, "p8": 3,
    "k1": 3, "k2": 2,
}


def add_test_profile(app, user_id, *, capital=TEST_PROFILE_CAPITAL, horizon=TEST_PROFILE_HORIZON, answers=None):
    """Save a current profile for ``user_id`` (as if the questionnaire was completed)."""
    with app.app_context():
        result = evaluate(load_questionnaire(), capital, horizon, answers or TEST_PROFILE_ANSWERS)
        return save_profile(user_id, result).id


def login_session(client, user_id: str) -> None:
    """Mark the test client's session as signed in (skips the sign-in form)."""
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def csrf_from(response) -> str:
    match = CSRF_PATTERN.search(response.data)
    if match is None:
        raise AssertionError(f"No CSRF token in the response of {response.request.path}")
    return match.group(1).decode()


class WebTestCase(unittest.TestCase):
    """App client signed in as a confirmed ``gestor`` (full access), with a CSRF token.

    Everything except the public pages needs a session, so page tests run as a
    signed-in user. Subclasses may change ``user_role`` / ``user_email``.
    """

    user_email = "tester@example.com"
    user_role = "gestor"

    def setUp(self) -> None:
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        self.user_id = create_test_user(
            self.app, self.user_email, role=self.user_role, role_confirmed=True, display_name="Tester"
        )
        login_session(self.client, self.user_id)
        response = self.client.get("/hrp")
        self.assertEqual(response.status_code, 200)
        self.csrf_token = csrf_from(response)

    def post(self, path: str, **data):
        data["csrf_token"] = self.csrf_token
        return self.client.post(path, data=data)
