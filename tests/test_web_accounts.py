"""Local accounts (SQLite + argon2), the global session guard and the backtest history."""

import json
import logging
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from optimizacion_portafolios.models.hrp_backtest import clear_backtest_cache
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import BacktestResult, BacktestRun, Portfolio, User
from optimizacion_portafolios.storage.users import get_user_by_email
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth import guard
from optimizacion_portafolios.web.auth.passwords import password_errors
from optimizacion_portafolios.web.auth.roles import current_gestors
from optimizacion_portafolios.web.auth.session import safe_next
from optimizacion_portafolios.web.auth.throttle import login_throttle
from support import TEST_PASSWORD, create_test_user, csrf_from, make_prices


class AccountTestCase(unittest.TestCase):
    """Fresh app (in-memory SQLite) with an anonymous client and a CSRF token.

    ``sign_in`` creates the local account if needed and signs in through the
    real form. Accounts listed in ``gestor_emails`` (comma-separated) are
    created as gestor, as ``flask users make-gestor`` would leave them.
    """

    gestor_emails = ""

    def setUp(self) -> None:
        login_throttle.clear()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        self.csrf_token = csrf_from(self.client.get("/auth/ingresar"))

    def post(self, path: str, **data):
        data["csrf_token"] = self.csrf_token
        return self.client.post(path, data=data)

    def create_user(self, email="ana@example.com", password=TEST_PASSWORD, **fields) -> str:
        listed = {item.strip().lower() for item in self.gestor_emails.split(",") if item.strip()}
        if email.strip().lower() in listed:
            fields.setdefault("role", "gestor")
            fields.setdefault("role_confirmed", True)
        return create_test_user(self.app, email, password, **fields)

    def user_id_of(self, email="ana@example.com") -> str:
        with self.app.app_context():
            return str(get_user_by_email(email).id)

    def user(self, email="ana@example.com") -> User:
        with self.app.app_context():
            user = get_user_by_email(email)
            db.session.expunge(user)
            return user

    def refresh_csrf(self) -> None:
        """Signing in or out rotates the session (and its CSRF token)."""
        with self.client.session_transaction() as session:
            signed_in = "_user_id" in session
        # Signed in, the page header carries the sign-out form; anonymous, the sign-in form does.
        path = "/" if signed_in else "/auth/ingresar"
        self.csrf_token = csrf_from(self.client.get(path, follow_redirects=True))

    def sign_in(self, email="ana@example.com", password=TEST_PASSWORD):
        self.create_user(email, password)
        response = self.post("/auth/ingresar", email=email, password=password)
        self.assertEqual(response.status_code, 302)
        self.refresh_csrf()
        return response

    def last_code(self, email: str) -> str:
        """6-digit code of the last email sent to ``email`` (test outbox)."""
        messages = [m for m in self.app.extensions["mail_outbox"] if m["to"] == email]
        self.assertTrue(messages, f"no email to {email}")
        return re.search(r"\b(\d{6})\b", messages[-1]["body"]).group(1)

    def register(self, email="nueva@example.com", password="clave2026", confirm=None, **extra):
        return self.post(
            "/auth/registro", email=email, password=password,
            password_confirm=password if confirm is None else confirm, **extra,
        )


class RegistrationTests(AccountTestCase):
    def test_register_creates_user_and_signs_in(self):
        response = self.register(email="  Nueva@Example.COM ", name="Nueva")
        # The email must be verified first: no session until the code is typed.
        self.assertEqual(urlsplit(response.location).path, "/auth/verificar")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        code = self.last_code("nueva@example.com")
        response = self.post("/auth/verificar", code=code)
        self.refresh_csrf()
        # A new account starts with the data notice.
        self.assertEqual(urlsplit(response.location).path, "/aviso-datos")
        user = self.user("nueva@example.com")
        self.assertEqual(user.email, "nueva@example.com")
        self.assertEqual(user.display_name, "Nueva")
        self.assertEqual((user.role, user.role_confirmed, user.plan), ("novato", False, "piloto"))
        self.assertIsNotNone(user.created_at)
        self.assertIsNotNone(user.last_login_at)
        with self.client.session_transaction() as session:
            self.assertEqual(session["_user_id"], str(user.id))
        html = self.client.get("/aviso-datos").data.decode()
        self.assertIn("Cerrar sesión", html)

    def test_password_is_stored_as_argon2_hash(self):
        self.register(password="clave2026")
        with self.app.app_context():
            stored = db.session.execute(db.text("SELECT password_hash FROM users")).scalar_one()
            raw = " ".join(
                str(value) for row in db.session.execute(db.text("SELECT * FROM users")) for value in row
            )
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn("clave2026", raw)

    def test_password_policy_and_confirmation(self):
        cases = {
            "corta1": "al menos 8 caracteres",
            "solamenteletras": "combinar letras y números",
            "12345678901": "combinar letras y números",
        }
        for password, message in cases.items():
            html = self.register(password=password).data.decode()
            self.assertIn(message, html, password)
        html = self.register(password="clave2026", confirm="clave2027").data.decode()
        self.assertIn("Las contraseñas no coinciden.", html)
        self.assertIn("no puede ser igual al correo", " ".join(password_errors("ana@ex1.com", "ana@ex1.com")))
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(db.func.count(User.id))), 0)

    def test_invalid_email_is_rejected(self):
        html = self.register(email="no-es-correo").data.decode()
        self.assertIn("Escribe un correo electrónico válido.", html)

    def test_existing_email_cannot_register_again(self):
        self.create_user("ana@example.com")
        html = self.register(email="ANA@example.com").data.decode()
        self.assertIn("Ya existe una cuenta con ese correo.", html)

    def test_password_never_appears_in_logs_or_flash(self):
        with self.assertLogs(level=logging.DEBUG) as captured:
            logging.getLogger("optimizacion_portafolios").info("start")
            self.register(password="clave2026")
            self.refresh_csrf()
            self.post("/auth/salir")
            self.refresh_csrf()
            failed = self.post("/auth/ingresar", email="nueva@example.com", password="mala2026x")
        self.assertNotIn("clave2026", "\n".join(captured.output))
        self.assertNotIn("mala2026x", failed.data.decode())


class LoginTests(AccountTestCase):
    def test_sign_in_and_menu(self):
        response = self.sign_in()
        self.assertEqual(urlsplit(response.location).path, "/")
        with self.client.session_transaction() as session:
            self.assertEqual(session["_user_id"], self.user_id_of())
            self.assertNotIn("auth", session)
        html = self.client.get("/").data.decode()
        self.assertIn("Mis portafolios", html)
        self.assertIn("Cerrar sesión", html)

    def test_email_is_case_insensitive(self):
        self.create_user("ana@example.com")
        response = self.post("/auth/ingresar", email=" ANA@Example.com ", password=TEST_PASSWORD)
        self.assertEqual(response.status_code, 302)

    def test_wrong_password_and_unknown_email_get_the_same_message(self):
        self.create_user("ana@example.com")
        wrong = self.post("/auth/ingresar", email="ana@example.com", password="mala1234").data.decode()
        unknown = self.post("/auth/ingresar", email="nadie@example.com", password="mala1234").data.decode()
        for html in (wrong, unknown):
            self.assertIn("Correo o contraseña incorrectos.", html)
            self.assertNotIn("no existe", html.lower())
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

    def test_next_is_followed_only_inside_the_app(self):
        self.create_user()
        response = self.post(
            "/auth/ingresar", email="ana@example.com", password=TEST_PASSWORD, next="/portafolios/"
        )
        self.assertEqual(urlsplit(response.location).path, "/portafolios/")
        with self.app.test_request_context():
            self.assertEqual(safe_next("/portafolios/"), "/portafolios/")
            for target in ("https://evil.example", "//evil.example", "javascript:alert(1)", "/\\evil.example"):
                self.assertEqual(safe_next(target), "/")

    def test_logout_clears_the_session(self):
        self.sign_in()
        self.post("/auth/salir")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        response = self.client.get("/portafolios/")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")

    def test_legacy_insforge_tokens_are_dropped(self):
        with self.client.session_transaction() as session:
            session["auth"] = {"access": "token", "refresh": "token", "user": {"id": "x"}}
            session["_user_id"] = "6f1c0d1e-0000-0000-0000-000000000000"
        response = self.client.get("/portafolios/")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")
        with self.client.session_transaction() as session:
            self.assertNotIn("auth", session)

    def test_outdated_hash_is_rehashed_on_sign_in(self):
        from argon2 import PasswordHasher

        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(TEST_PASSWORD)
        self.create_user()
        with self.app.app_context():
            get_user_by_email("ana@example.com").password_hash = weak
            db.session.commit()
        self.post("/auth/ingresar", email="ana@example.com", password=TEST_PASSWORD)
        self.assertNotEqual(self.user().password_hash, weak)
        self.assertTrue(self.user().password_hash.startswith("$argon2id$"))

    def test_repeated_failures_block_sign_in(self):
        self.create_user()
        for _ in range(5):
            self.post("/auth/ingresar", email="ana@example.com", password="mala1234")
        response = self.post("/auth/ingresar", email="ana@example.com", password=TEST_PASSWORD)
        self.assertIn("Demasiados intentos fallidos", response.data.decode())
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        login_throttle.clear()
        self.assertEqual(
            self.post("/auth/ingresar", email="ana@example.com", password=TEST_PASSWORD).status_code, 302
        )


class GestorRoleTests(AccountTestCase):
    """gestor is granted only from the command line, to a single account."""

    def cli(self, *args):
        return self.app.test_cli_runner().invoke(args=["users", *args])

    def test_gestor_cannot_be_chosen_in_the_form(self):
        self.register(email="ana@example.com", role="gestor", role_confirmed="1")
        user = self.user("ana@example.com")
        self.assertEqual((user.role, user.role_confirmed), ("novato", False))

    def test_make_gestor_is_the_only_way_and_there_is_one(self):
        self.create_user("jefa@example.com")
        self.create_user("otro@example.com")
        result = self.cli("make-gestor", "JEFA@example.com")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual((self.user("jefa@example.com").role, self.user("jefa@example.com").role_confirmed), ("gestor", True))
        blocked = self.cli("make-gestor", "otro@example.com")
        self.assertNotEqual(blocked.exit_code, 0)
        self.assertIn("Ya hay una cuenta gestor", blocked.output)
        replaced = self.cli("make-gestor", "otro@example.com", "--replace")
        self.assertEqual(replaced.exit_code, 0, replaced.output)
        self.assertEqual(self.user("jefa@example.com").role, "novato")
        with self.app.app_context():
            self.assertEqual([user.email for user in current_gestors()], ["otro@example.com"])
        self.assertIn("otro@example.com", self.cli("list-gestor").output)

    def test_revoke_and_unknown_accounts(self):
        self.create_user("jefa@example.com", role="gestor", role_confirmed=True)
        self.assertEqual(self.cli("revoke-gestor", "jefa@example.com").exit_code, 0)
        self.assertEqual((self.user("jefa@example.com").role, self.user("jefa@example.com").role_confirmed), ("novato", False))
        self.assertNotEqual(self.cli("make-gestor", "nadie@example.com").exit_code, 0)

    def test_registration_never_creates_a_gestor(self):
        self.register(email="jefa@example.com")
        self.assertEqual(self.user("jefa@example.com").role, "novato")


class GlobalGuardTests(AccountTestCase):
    PUBLIC = {"main.index", "auth.login", "auth.register", "auth.verify", "auth.resend_verification", "auth.forgot_password", "auth.reset_password"}

    def _assert_blocked(self, response, path):
        if path.startswith("/api/"):
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(set(json.loads(response.data)), {"error"}, path)
        else:
            self.assertEqual(response.status_code, 302, path)
            location = urlsplit(response.location)
            self.assertEqual(location.path, "/auth/ingresar", path)

    def test_every_route_requires_a_session(self):
        checked = 0
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == "static" or rule.endpoint.endswith(".static"):
                continue
            values = {argument: "1" for argument in rule.arguments}
            with self.app.test_request_context():
                from flask import url_for

                path = url_for(rule.endpoint, **values)
            methods = rule.methods - {"HEAD", "OPTIONS"}
            for method in sorted(methods):
                if rule.endpoint in self.PUBLIC:
                    continue
                data = {"csrf_token": self.csrf_token} if method == "POST" else None
                response = self.client.open(path, method=method, data=data)
                self._assert_blocked(response, path)
                checked += 1
            if rule.endpoint in self.PUBLIC and "GET" in methods:
                response = self.client.get(path)
                # Public pages render, or send back to another auth page when
                # nothing is pending (e.g. /auth/verificar without an account).
                if response.status_code == 302:
                    self.assertTrue(urlsplit(response.location).path.startswith("/auth/"), path)
                else:
                    self.assertEqual(response.status_code, 200, path)
        self.assertGreater(checked, 20)

    @patch("optimizacion_portafolios.web.history.services.download_prices")
    def test_json_endpoints_return_401_without_data(self, download):
        for path in ("/api/historial?ticker=AAPL", "/api/ohlc?ticker=AAPL&start=2024-01-01"):
            response = self.client.get(path)
            self._assert_blocked(response, path)
            self.assertNotIn(b"AAPL", response.data)
        download.assert_not_called()

    def test_page_redirect_keeps_next(self):
        response = self.client.get("/cuenta/backtests")
        location = urlsplit(response.location)
        self.assertEqual(location.path, "/auth/ingresar")
        self.assertEqual(parse_qs(location.query)["next"], ["/cuenta/backtests"])
        login = self.client.get(response.location).data.decode()
        self.assertIn("Inicia sesión para continuar.", login)

    def test_static_files_and_portada_are_public(self):
        self.assertEqual(self.client.get("/static/css/app.css").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_access_checks_extension_point(self):
        calls = []

        def check(endpoint):
            calls.append(endpoint)
            return ("bloqueado", 403) if endpoint == "portfolios.index" else None

        guard.register_access_check(self.app, check)
        self.client.get("/")  # public: not checked
        self.assertEqual(self.client.get("/portafolios/").status_code, 302)  # anonymous: not checked
        self.assertEqual(calls, [])
        self.sign_in()
        self.assertEqual(self.client.get("/portafolios/").status_code, 403)
        self.assertEqual(self.client.get("/glosario").status_code, 200)
        self.post("/auth/salir")  # exempt from the checks
        self.assertEqual(calls, ["portfolios.index", "main.glossary"])


class BacktestHistoryTests(AccountTestCase):
    # Backtests are gestor-only: ana and eva are gestores.
    gestor_emails = "ana@example.com, eva@example.com"

    def _post_backtest(self):
        return self.post(
            "/hrp", action="optimize", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
            start_date="2024-01-01", tickers=["AAPL", "MSFT", "XLV"], custom_tickers="",
            backtest="1", bt_lookback="60",
        ).data.decode()

    def _runs(self):
        with self.app.app_context():
            return db.session.execute(select(BacktestRun.id, BacktestRun.user_id, BacktestRun.tickers)).all()

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_backtest_is_recorded_once_per_inputs(self, download):
        clear_backtest_cache()
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        self.sign_in()
        html = self._post_backtest()
        self.assertIn("Backtest guardado en tu historial.", html)
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        run_id, owner, tickers = runs[0]
        self.assertEqual((owner, tickers), (self.user_id_of(), ["AAPL", "MSFT", "XLV"]))
        with self.app.app_context():
            results = db.session.scalars(select(BacktestResult)).all()
            self.assertEqual(len(results), 7)
            monthly = next(row for row in results if row.strategy == "Mensual")
            self.assertEqual(monthly.rebalances, len(monthly.rebalance_dates))

        again = self._post_backtest()
        self.assertIn("Este backtest ya estaba en tu historial.", again)
        self.assertEqual(len(self._runs()), 1)

        listing = self.client.get("/cuenta/backtests").data.decode()
        self.assertIn("AAPL, MSFT, XLV", listing)
        detail = self.client.get(f"/cuenta/backtests/{run_id}").data.decode()
        self.assertIn("Resultados por estrategia", detail)
        self.assertIn("Mensual", detail)
        self.post(f"/cuenta/backtests/{run_id}/eliminar")
        self.assertEqual(self._runs(), [])
        with self.app.app_context():
            self.assertEqual(db.session.scalars(select(BacktestResult)).all(), [])

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_other_users_cannot_see_or_delete_a_run(self, download):
        clear_backtest_cache()
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        self.sign_in()
        self._post_backtest()
        run_id = self._runs()[0][0]
        self.post("/auth/salir")
        self.refresh_csrf()
        self.sign_in("eva@example.com")
        self.assertEqual(self.client.get(f"/cuenta/backtests/{run_id}").status_code, 404)
        self.assertNotIn("AAPL, MSFT, XLV", self.client.get("/cuenta/backtests").data.decode())
        self.post(f"/cuenta/backtests/{run_id}/eliminar")
        self.assertEqual(len(self._runs()), 1)

    def test_invalid_run_id_is_404(self):
        self.sign_in()
        self.assertEqual(self.client.get("/cuenta/backtests/not-a-uuid").status_code, 404)


class ReassignCommandTests(AccountTestCase):
    def test_reassign_portfolios_to_a_local_account(self):
        self.create_user()
        old = "6f1c0d1e-1234-4abc-9def-000000000000"
        with self.app.app_context():
            from datetime import date

            db.session.add(
                Portfolio(user_id=old, name="Viejo", model="hrp", benchmark="SPY",
                          initial_capital=1000, tracking_start=date(2026, 1, 5))
            )
            db.session.commit()
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["users", "reassign-portfolios", old, "ANA@example.com"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("portfolios: 1", result.output)
        with self.app.app_context():
            owners = db.session.scalars(select(Portfolio.user_id)).all()
        self.assertEqual(owners, [self.user_id_of()])
        missing = runner.invoke(args=["users", "reassign-portfolios", old, "nadie@example.com"])
        self.assertNotEqual(missing.exit_code, 0)
        self.assertIn("No existe una cuenta local", missing.output)


if __name__ == "__main__":
    unittest.main()
