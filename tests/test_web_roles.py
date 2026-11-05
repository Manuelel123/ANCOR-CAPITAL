"""Roles: endpoint x role matrix, guided pages, comparison, notices and the portada."""

import json
import re
import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

from optimizacion_portafolios.storage import db, repositories
from optimizacion_portafolios.storage.models import AlertRule, Portfolio
from optimizacion_portafolios.storage.repositories import HoldingData
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth import guard, permissions
from optimizacion_portafolios.web.common import guided
from optimizacion_portafolios.web.portfolios.snapshots import sign
from support import create_test_user, csrf_from, login_session, make_prices

NOTICE = "Información educativa. No es recomendación de inversión ni asesoría financiera."
ROLES = ("novato", "inversionista", "gestor")
MARKET_CAPS = {
    "AAPL": 3.0e12,
    "MSFT": 3.5e12,
    "GOOGL": 2.0e12,
    "BTC-USD": 1.2e12,
}
OFFLINE = requests.ConnectionError("sin red en las pruebas")


def notice_block(html: str) -> str | None:
    """Highlighted sentence of the first educational-notice element (not the footer)."""
    match = re.search(r'class="notice notice--warning educational-notice[^"]*"[^>]*>\s*<p><strong>(.*?)</strong>', html, re.S)
    return match.group(1).strip() if match else None


def priced(tickers, _start, _end):
    return make_prices(tuple(tickers))


class RoleTestCase(unittest.TestCase):
    """Client signed in with ``role`` (stored in the database, like the real app)."""

    role = "novato"

    def setUp(self):
        email = f"{self.role}@example.com"
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        self.user_id = create_test_user(self.app, email, role=self.role, display_name=self.role.capitalize())
        login_session(self.client, self.user_id)
        self.csrf_token = csrf_from(self.client.get("/"))

    def post(self, path, **data):
        data["csrf_token"] = self.csrf_token
        return self.client.post(path, data=data)

    def assert_denied(self, response, path=""):
        if path.startswith("/api/"):
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(set(json.loads(response.data)), {"error"}, path)
        else:
            self.assertEqual(response.status_code, 302, path)
            self.assertEqual(urlsplit(response.location).path, "/", path)

    def portfolio(self) -> int:
        with self.app.app_context():
            return repositories.create_portfolio(
                self.user_id, name="Seguido", model="hrp", benchmark="SPY", initial_capital=10_000,
                tracking_start=date(2026, 1, 5),
                holdings=[HoldingData("AAPL", 0.5, 30.0, 150.0), HoldingData("MSFT", 0.5, 15.0, 300.0)],
                params={"universe": "Acciones y Bitcoin"},
            ).id

    def rules(self) -> list:
        with self.app.app_context():
            return sorted(
                (rule.kind, rule.yellow, rule.red, rule.enabled) for rule in db.session.query(AlertRule).all()
            )

    def snapshot_token(self) -> str:
        with self.app.test_request_context():
            return sign({"model": "hrp", "benchmark": "SPY", "weights": {"AAPL": 0.5, "MSFT": 0.5}, "metrics": {}, "params": {}})


# Expected access per page (written out, not derived from the table under test).
PAGES = {
    "/": ROLES,
    "/glosario": ROLES,
    "/historial": ROLES,
    "/perfil/": ROLES,
    "/hrp": ROLES,
    "/black-litterman": ("gestor",),
    "/portafolios/": ROLES,
    "/comparar": ("gestor",),
    "/monitoring": ("gestor",),
    "/cuenta/backtests": ("gestor",),
    "/api/historial?ticker=%3C%3E": ("gestor",),
    "/api/ohlc?ticker=%3C%3E": ("gestor",),
}


class PermissionTableTests(unittest.TestCase):
    def test_every_endpoint_is_classified(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        missing = [
            rule.endpoint
            for rule in app.url_map.iter_rules()
            if not guard.is_public_endpoint(rule.endpoint)
            and rule.endpoint not in permissions.ENDPOINT_ROLES
            and not rule.endpoint.startswith(permissions.OPEN_PREFIXES)
        ]
        self.assertEqual(missing, [])

    def test_unknown_endpoints_are_gestor_only(self):
        self.assertEqual(permissions.allowed_roles("nuevo.endpoint"), permissions.MANAGERS)
        self.assertEqual(permissions.allowed_roles("portfolios.alerts", "GET"), permissions.EVERYONE)
        self.assertEqual(permissions.allowed_roles("portfolios.alerts", "POST"), permissions.INVESTORS)

    def test_feature_tiers(self):
        expected = {
            "advanced_params": ("gestor",),
            "backtest": ("gestor",),
            "signals": ("gestor",),
            "market": ("gestor",),
            "weight_limits": ("inversionista", "gestor"),
            "exclude_assets": ("inversionista", "gestor"),
            "compare": ("gestor",),
            "fix_portfolio": ("inversionista", "gestor"),
            "manage_portfolio": ("inversionista", "gestor"),
            "edit_alerts": ("inversionista", "gestor"),
        }
        for feature, roles in expected.items():
            for role in ROLES:
                self.assertEqual(permissions.can(feature, role), role in roles, (feature, role))


class MatrixMixin:
    def test_page_matrix(self):
        for path, roles in PAGES.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                if self.role in roles:
                    self.assertNotIn(response.status_code, (302, 401, 403), path)
                else:
                    self.assert_denied(response, path)

    def test_navigation_shows_only_allowed_pages(self):
        html = self.client.get("/").data.decode()
        nav = re.search(r'<nav class="site-nav"[^>]*>(.*?)</nav>', html, re.S).group(1)
        links = set(re.findall(r'href="([^"]+)"', nav))
        expected = {"/", "/hrp", "/portafolios/"}
        if self.role == "gestor":
            expected |= {"/monitoring", "/black-litterman"}  # the original menu
        self.assertEqual(links, expected)
        self.assertEqual("/cuenta/backtests" in html, self.role == "gestor")


class NovatoTests(MatrixMixin, RoleTestCase):
    role = "novato"

    def test_denied_page_explains_the_role(self):
        response = self.client.get("/monitoring", follow_redirects=True)
        self.assertIn("Tu rol (Novato) no incluye esta función.", response.data.decode())

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=OFFLINE)
    def test_portfolios_are_read_only(self, _download):
        portfolio_id = self.portfolio()
        detail = self.client.get(f"/portafolios/{portfolio_id}").data.decode()
        self.assertIn("Seguido", detail)
        self.assertIn("Panel", detail)
        self.assertNotIn("/gestionar", detail)
        self.assertNotIn("/senales", detail)
        alerts = self.client.get(f"/portafolios/{portfolio_id}/alertas").data.decode()
        self.assertIn("Solo lectura", alerts)
        self.assertNotIn("Guardar umbrales", alerts)
        self.assertEqual(self.client.get(f"/portafolios/{portfolio_id}/versiones").status_code, 200)

        # Every write, called directly, is blocked and changes nothing.
        rules_before = self.rules()
        blocked = [
            ("get", f"/portafolios/{portfolio_id}/gestionar", {}),
            ("get", f"/portafolios/{portfolio_id}/senales", {}),
            ("post", f"/portafolios/{portfolio_id}/alertas", {"alerts_form": "1", "alert_drawdown_enabled": "1", "alert_drawdown_yellow": "1", "alert_drawdown_red": "2"}),
            ("post", f"/portafolios/{portfolio_id}/gestionar/vista-previa", {"action": "rebalance", "rebalance_method": "targets"}),
            ("post", f"/portafolios/{portfolio_id}/gestionar/confirmar", {"token": "x"}),
            ("post", f"/portafolios/{portfolio_id}/archivar", {}),
            ("post", "/portafolios/fijar", {"snapshot": self.snapshot_token(), "step": "confirm", "name": "Nuevo"}),
        ]
        for method, path, data in blocked:
            with self.subTest(path=path, method=method):
                response = self.post(path, **data) if method == "post" else self.client.get(path)
                self.assert_denied(response, path)
        with self.app.app_context():
            portfolios = db.session.query(Portfolio).all()
            self.assertEqual([(p.name, p.status) for p in portfolios], [("Seguido", "active")])
        self.assertEqual(self.rules(), rules_before)

    def test_guided_hrp_form_has_no_advanced_parameters(self):
        html = self.client.get("/hrp").data.decode()
        self.assertIn("Vista guiada", html)
        for field in ('name="start_date"', 'name="min_weight"', 'name="max_weight"', 'name="custom_tickers"',
                      'name="backtest"', 'name="benchmark"', 'name="tickers"'):
            self.assertNotIn(field, html, field)
        self.assertIn('name="universe" value="Acciones y Bitcoin"', html)
        self.assertIn("Glosario", html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=priced)
    def test_guided_hrp_ignores_injected_parameters(self, download):
        response = self.post(
            "/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT"],
            custom_tickers="NVDA", start_date="2020-01-01", benchmark="Nasdaq 100 (QQQ)",
            custom_benchmark="QQQ", min_weight="40", max_weight="45", max_AAPL="5", backtest="1",
            bt_lookback="60",
        )
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        tickers, start, _end = download.call_args_list[0].args
        self.assertEqual(tickers, ("AAPL", "MSFT", "GOOGL", "BTC-USD"))
        self.assertEqual(start, guided.guided_start())
        self.assertEqual(download.call_args_list[1].args[0], ("SPY",))
        self.assertEqual(download.call_count, 2)  # no backtest download
        self.assertEqual(notice_block(html), NOTICE)
        self.assertIn("¿Por qué cada activo recibe ese peso?", html)
        self.assertIn("volatilidad anual", html)
        self.assertIn("correlación promedio con el resto", html)
        self.assertIn("Diversificación", html)  # glossary
        self.assertNotIn("Establecer como portafolio óptimo", html)
        self.assertNotIn("Backtest de rebalanceo", html)
        weights = [float(value) for value in re.findall(r'<td class="num">(\d+\.\d+)%</td>', html)]
        self.assertAlmostEqual(sum(weights), 100.0, places=1)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=priced)
    def test_report_download_is_not_available(self, _download):
        response = self.post("/hrp", action="download_report", universe="Acciones y Bitcoin")
        self.assertEqual(response.mimetype, "text/html")
        self.assertNotIn("Content-Disposition", response.headers)



class InversionistaTests(MatrixMixin, RoleTestCase):
    role = "inversionista"

    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=priced)
    def test_can_exclude_assets_and_cap_weights(self, download):
        html = self.post(
            "/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "BTC-USD", "NVDA"],
            max_weight="40", start_date="2020-01-01", min_weight="30",
        ).data.decode()
        self.assertEqual(download.call_args_list[0].args[0], ("AAPL", "MSFT", "BTC-USD"))
        self.assertEqual(download.call_args_list[0].args[1], guided.guided_start())
        weights = [float(value) for value in re.findall(r'<td class="num">(\d+\.\d+)%</td>', html)]
        self.assertEqual(len(weights), 3)
        self.assertTrue(all(value <= 40.01 for value in weights), weights)
        self.assertIn("Establecer como portafolio óptimo", html)
        self.assertIn('name="max_weight"', html)
        self.assertNotIn('name="start_date"', html)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=OFFLINE)
    def test_can_edit_alerts_and_manage_but_not_signals(self, _download):
        portfolio_id = self.portfolio()
        response = self.post(f"/portafolios/{portfolio_id}/alertas", alerts_form="1", alert_drawdown_enabled="1",
                             alert_drawdown_yellow="12", alert_drawdown_red="20")
        self.assertEqual(urlsplit(response.location).path, f"/portafolios/{portfolio_id}/alertas")
        self.assertEqual(self.client.get(f"/portafolios/{portfolio_id}/gestionar").status_code, 200)
        self.assert_denied(self.client.get(f"/portafolios/{portfolio_id}/senales"))
        self.assertNotIn("/senales", self.client.get(f"/portafolios/{portfolio_id}").data.decode())



class GestorTests(MatrixMixin, RoleTestCase):
    role = "gestor"

    def test_full_pages_keep_every_parameter(self):
        hrp = self.client.get("/hrp").data.decode()
        for field in ('name="start_date"', 'name="min_weight"', 'name="backtest"', 'name="custom_tickers"'):
            self.assertIn(field, hrp)
        bl = self.client.get("/black-litterman").data.decode()
        self.assertIn('name="objective"', bl)
        self.assertIn("Opciones", self.client.get("/monitoring").data.decode())

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=OFFLINE)
    def test_original_mode_has_no_educational_additions(self, _download):
        """The gestor sees the pages exactly as before the educational layer."""
        portfolio_id = self.portfolio()
        for path in ("/hrp", "/black-litterman", "/monitoring", "/portafolios/",
                     f"/portafolios/{portfolio_id}", f"/portafolios/{portfolio_id}/alertas",
                     f"/portafolios/{portfolio_id}/versiones", f"/portafolios/{portfolio_id}/gestionar",
                     "/cuenta/backtests", "/"):
            with self.subTest(path=path):
                html = self.client.get(path).data.decode()
                self.assertIsNone(notice_block(html), path)
                self.assertNotIn("Mi perfil", html, path)
                self.assertNotIn("Glosario", html, path)


class OnlyHrpTests(unittest.TestCase):
    def test_only_hrp_is_offered_to_novato_and_inversionista(self):
        for endpoint in ("black_litterman.black_litterman", "compare.compare"):
            for role in ("novato", "inversionista"):
                self.assertFalse(permissions.role_allows(role, endpoint))
            self.assertTrue(permissions.role_allows("gestor", endpoint))


class NoBlackLittermanForLearnersTests(unittest.TestCase):
    PAGES = ("/", "/hrp", "/portafolios/", "/historial", "/glosario", "/perfil/", "/introduccion")

    def client_for(self, role):
        app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        client = app.test_client()
        login_session(client, create_test_user(app, f"{role}@example.com", role=role, role_confirmed=True))
        return client

    def test_no_page_links_to_it(self):
        for role in ("novato", "inversionista"):
            client = self.client_for(role)
            for path in self.PAGES:
                with self.subTest(role=role, path=path):
                    html = client.get(path).data.decode()
                    self.assertNotIn("Black-Litterman", html)
                    self.assertNotIn('href="/comparar"', html)

    def test_the_gestor_keeps_it_in_mis_portafolios(self):
        html = self.client_for("gestor").get("/portafolios/").data.decode()
        self.assertIn('href="/black-litterman"', html)


class PortadaTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()

    def test_portada_shows_notice_and_forms_but_no_results(self):
        html = self.client.get("/").data.decode()
        self.assertIn(NOTICE, notice_block(html))
        self.assertIn("personas jóvenes", html)
        self.assertIn('action="/auth/ingresar"', html)
        self.assertIn('action="/auth/registro"', html)
        self.assertIn('name="password_confirm"', html)
        for result_marker in ('class="results"', "data-plotly", "Resultado HRP", "Distribución del portafolio",
                              "metric-card__value", 'href="/hrp"', 'href="/portafolios/"'):
            self.assertNotIn(result_marker, html, result_marker)

    def test_portada_login_form_signs_in(self):
        create_test_user(self.app, "ana@example.com")
        token = csrf_from(self.client.get("/"))
        response = self.client.post("/auth/ingresar", data={"csrf_token": token, "email": "ana@example.com", "password": "secreto1"})
        self.assertEqual(urlsplit(response.location).path, "/")
        html = self.client.get("/").data.decode()
        self.assertIn("Hola, Ana", html)
        self.assertIn("Tu perfil vigente: Moderado", html)
        self.assertIn("Factor limitante: Puntaje", html)
        self.assertIn("Novato", html)

    def test_signed_in_without_profile_sees_no_tools(self):
        user_id = create_test_user(self.app, "nuevo@example.com", profile=False)
        login_session(self.client, user_id)
        html = self.client.get("/").data.decode()
        self.assertIn("Completa tu perfil de riesgo", html)
        self.assertNotIn('class="module-card"', html)


if __name__ == "__main__":
    unittest.main()
