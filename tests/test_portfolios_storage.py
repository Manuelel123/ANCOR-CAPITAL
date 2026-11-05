"""F1: storage layer, ownership checks, Flask-Login and the sign-in throttle."""

import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import func, select

from optimizacion_portafolios.storage import db, repositories
from optimizacion_portafolios.storage.models import DailySnapshot, Holding, PortfolioVersion, Transaction
from optimizacion_portafolios.storage.repositories import HoldingData, TransactionData
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth.throttle import LoginThrottle
from support import create_test_user, csrf_from, login_session
from test_web_accounts import AccountTestCase


def create_sample(user_id: str, name: str = "Mi óptimo"):
    return repositories.create_portfolio(
        user_id,
        name=name,
        model="hrp",
        benchmark="SPY",
        initial_capital=100_000,
        tracking_start=date(2026, 1, 5),
        holdings=[
            HoldingData("AAPL", 0.6, 300.0, 200.0),
            HoldingData("MSFT", 0.4, 100.0, 400.0),
        ],
        transactions=[
            TransactionData("AAPL", "buy", 300.0, 200.0, 60_000),
            TransactionData("MSFT", "buy", 100.0, 400.0, 40_000),
        ],
        params={"universe": "Portafolio actual"},
    )


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_create_portfolio_stores_version_one_with_holdings(self):
        portfolio = create_sample("user-a")
        version = portfolio.current_version
        self.assertEqual((version.number, version.reason), (1, "fix"))
        self.assertEqual(version.target_weights, {"AAPL": 0.6, "MSFT": 0.4})
        self.assertEqual(version.units["AAPL"], 300.0)
        self.assertEqual(len(version.transactions), 2)
        self.assertEqual(version.params["universe"], "Portafolio actual")

    def test_reads_are_scoped_to_the_owner(self):
        mine = create_sample("user-a")
        create_sample("user-b", "Ajeno")
        self.assertEqual([p.name for p in repositories.list_portfolios("user-a")], ["Mi óptimo"])
        self.assertIsNotNone(repositories.get_portfolio("user-a", mine.id))
        self.assertIsNone(repositories.get_portfolio("user-b", mine.id))

    def test_versions_are_numbered_and_cannot_go_back_in_time(self):
        portfolio = create_sample("user-a")
        version = repositories.add_version(
            portfolio, effective_date=date(2026, 2, 2), reason="rebalance",
            holdings=[HoldingData("AAPL", 0.5, 240.0, 210.0), HoldingData("MSFT", 0.5, 120.0, 420.0)],
            transactions=[], value_before=100_800, value_after=100_700, cost=100, turnover=0.1,
        )
        self.assertEqual(version.number, 2)
        self.assertEqual(portfolio.current_version.target_weights["AAPL"], 0.5)
        with self.assertRaises(ValueError):
            repositories.add_version(
                portfolio, effective_date=date(2026, 1, 1), reason="rebalance",
                holdings=[HoldingData("AAPL", 1.0, 1.0, 1.0)], transactions=[],
                value_before=1, value_after=1, cost=0, turnover=0,
            )

    def test_deleting_a_portfolio_cascades(self):
        portfolio = create_sample("user-a")
        db.session.delete(portfolio)
        db.session.commit()
        self.assertEqual(db.session.query(PortfolioVersion).count(), 0)
        self.assertEqual(db.session.query(Holding).count(), 0)

    def test_archive_keeps_history(self):
        portfolio = create_sample("user-a")
        repositories.archive_portfolio(portfolio, None)
        self.assertEqual(repositories.active_portfolios(), [])
        self.assertEqual(len(repositories.list_portfolios("user-a")), 1)

    def test_snapshot_stored_concurrently_is_refreshed_not_duplicated(self):
        portfolio = create_sample("user-a")
        day = date(2026, 1, 5)
        row = {"day": day, "value": 100_000.0, "benchmark_value": None, "weights": {}, "metrics": None}
        repositories.save_snapshots(portfolio, [row], refresh_day=day)
        # The first read misses the row another request just committed.
        stale = [{}, repositories._stored_snapshots(portfolio.id)]
        with patch.object(repositories, "_stored_snapshots", side_effect=stale):
            written = repositories.save_snapshots(portfolio, [{**row, "value": 101_000.0}], refresh_day=day)
        self.assertEqual(written, 1)
        snapshots = repositories.latest_snapshots(portfolio)
        self.assertEqual([s.value for s in snapshots], [101_000.0])


class PortfolioPagesTests(AccountTestCase):
    def _own_id(self):
        return self.user_id_of()

    def test_pages_require_sign_in(self):
        response = self.client.get("/portafolios/")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")

    def test_signed_in_user_sees_nav_link_and_empty_state(self):
        self.assertNotIn('href="/portafolios/"', self.client.get("/").data.decode())
        self.sign_in()
        html = self.client.get("/portafolios/").data.decode()
        self.assertIn("Aún no tienes portafolios en seguimiento", html)
        self.assertIn('href="/portafolios/"', self.client.get("/").data.decode())
        self.assertIn("Capital nocional", html)

    def test_owner_sees_portfolio_and_others_get_404(self):
        self.sign_in()
        with self.app.app_context():
            mine = create_sample(self._own_id()).id
            other = create_sample("someone-else", "Ajeno").id
        html = self.client.get(f"/portafolios/{mine}").data.decode()
        self.assertIn("Mi óptimo", html)
        self.assertIn("AAPL", html)
        self.assertEqual(self.client.get(f"/portafolios/{other}").status_code, 404)
        self.assertNotIn("Ajeno", self.client.get("/portafolios/").data.decode())

    def test_flask_login_tracks_the_local_account(self):
        self.sign_in()
        with self.client.session_transaction() as session:
            self.assertEqual(session["_user_id"], self.user_id_of())
        self.post("/auth/salir")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)


class ThrottleTests(unittest.TestCase):
    def test_window_and_reset(self):
        throttle = LoginThrottle(max_failures=2, window=60)
        throttle.record_failure("email:a")
        self.assertEqual(throttle.retry_after("email:a"), 0)
        throttle.record_failure("email:a")
        self.assertGreater(throttle.retry_after("email:a", "ip:1"), 0)
        throttle.reset("email:a")
        self.assertEqual(throttle.retry_after("email:a"), 0)


def _rising_prices(tickers, start, end):
    index = pd.bdate_range(start, end)
    return pd.DataFrame({ticker: [100.0 + day for day in range(len(index))] for ticker in tickers}, index=index)


class GestorPortfolioAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.gestor_id = create_test_user(self.app, "jefa@example.com", role="gestor", role_confirmed=True)
        self.novato_id = create_test_user(self.app, "ana@example.com")
        with self.app.app_context():
            self.portfolio_id = self._portfolio("Portafolio 1").id
            repositories.save_snapshots(
                repositories.get_portfolio(self.gestor_id, self.portfolio_id),
                [{"day": date(2026, 11, 2), "value": 110000.0, "benchmark_value": 105000.0,
                  "weights": {"AAPL": 1.0}, "metrics": None}],
            )

    def _portfolio(self, name, user_id=None):
        return repositories.create_portfolio(
            user_id or self.gestor_id, name=name, model="hrp", benchmark="SPY", initial_capital=100000.0,
            tracking_start=date(2026, 10, 1), holdings=[HoldingData("AAPL", 1.0, 10.0, 100.0)],
            transactions=[TransactionData("AAPL", "buy", 10.0, 100.0, 1000.0)],
        )

    def client_for(self, user_id):
        client = self.app.test_client()
        login_session(client, user_id)
        return client

    def post(self, client, path, **data):
        data["csrf_token"] = csrf_from(client.get("/portafolios/"))
        return client.post(path, data=data, follow_redirects=True)

    def stored(self):
        with self.app.app_context():
            return repositories.get_portfolio(self.gestor_id, self.portfolio_id)

    def test_the_gestor_can_rename(self):
        html = self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/renombrar",
                         name="  PAVF:   Portafolio Gestor 1 ").data.decode()
        self.assertIn("PAVF: Portafolio Gestor 1", html)
        self.assertEqual(self.stored().name, "PAVF: Portafolio Gestor 1")

    def test_an_empty_name_is_rejected(self):
        self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/renombrar", name="   ")
        self.assertEqual(self.stored().name, "Portafolio 1")

    def test_liquidating_keeps_the_history(self):
        self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/archivar")
        portfolio = self.stored()
        self.assertEqual(portfolio.status, "archived")

    def test_deleting_needs_the_exact_name(self):
        self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/eliminar", confirmacion="otro")
        self.assertIsNotNone(self.stored())

    def test_deleting_removes_everything(self):
        self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/eliminar",
                  confirmacion="Portafolio 1")
        self.assertIsNone(self.stored())
        with self.app.app_context():
            for model in (PortfolioVersion, Holding, Transaction, DailySnapshot):
                self.assertEqual(db.session.scalar(select(func.count()).select_from(model)), 0, model.__name__)

    def test_the_gestor_cannot_touch_a_portfolio_of_someone_else(self):
        with self.app.app_context():
            other = self._portfolio("Ajeno", self.novato_id).id
        client = self.client_for(self.gestor_id)
        token = csrf_from(client.get("/portafolios/"))
        response = client.post(f"/portafolios/{other}/eliminar", data={"csrf_token": token, "confirmacion": "Ajeno"})
        self.assertEqual(response.status_code, 404)

    def test_other_roles_have_no_controls_and_no_access(self):
        client = self.client_for(self.novato_id)
        self.assertNotIn("Administrar", client.get("/portafolios/").data.decode())
        token = csrf_from(client.get("/portafolios/"))
        for action, data in (("renombrar", {"name": "x"}), ("visibilidad", {"visible": "1"}),
                             ("eliminar", {"confirmacion": "Portafolio 1"})):
            response = client.post(f"/portafolios/{self.portfolio_id}/{action}", data={"csrf_token": token, **data})
            self.assertEqual(response.status_code, 302, action)
        self.assertEqual(self.stored().name, "Portafolio 1")

    def test_users_only_see_what_the_gestor_shows_as_a_summary(self):
        novato = self.client_for(self.novato_id)
        self.assertNotIn("Ver los portafolios del gestor", novato.get("/").data.decode())
        self.post(self.client_for(self.gestor_id), f"/portafolios/{self.portfolio_id}/visibilidad", visible="1")
        html = novato.get("/").data.decode()
        self.assertIn("Ver los portafolios del gestor", html)
        self.assertIn("Portafolio 1", html)
        self.assertIn("10.00 %", html)
        self.assertIn("5.00 %", html)
        self.assertIn("2026-11-02", html)
        self.assertIn("AAPL 100.0 %", html)
        self.assertIn(f"/portafolios/gestor/{self.portfolio_id}", html)
        self.assertEqual(novato.get(f"/portafolios/{self.portfolio_id}").status_code, 404)

    def test_the_card_shows_the_latest_valuation(self):
        with self.app.app_context():
            portfolio = repositories.get_portfolio(self.gestor_id, self.portfolio_id)
            repositories.save_snapshots(portfolio, [
                {"day": date(2026, 10, 19), "value": 90000.0, "benchmark_value": 95000.0,
                 "weights": {"AAPL": 1.0}, "metrics": None},
                {"day": date(2026, 11, 4), "value": 120000.0, "benchmark_value": 101000.0,
                 "weights": {"AAPL": 1.0}, "metrics": None},
            ])
            repositories.set_portfolio_visibility(portfolio, True)
        html = self.client_for(self.novato_id).get("/").data.decode()
        self.assertIn("2026-11-04", html)
        self.assertIn("20.00 %", html)
        self.assertNotIn("2026-10-19", html)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_rising_prices)
    def test_users_see_the_live_panel_and_can_only_look(self, _download):
        with self.app.app_context():
            repositories.set_portfolio_visibility(
                repositories.get_portfolio(self.gestor_id, self.portfolio_id), True
            )
        path = f"/portafolios/gestor/{self.portfolio_id}"
        response = self.client_for(self.novato_id).get(path)
        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Portafolio 1", html)
        self.assertIn("Portafolio del gestor", html)
        self.assertIn("Solo puedes verlo", html)
        self.assertIn("AAPL", html)
        self.assertNotIn("No se pudo valorar", html)
        for link in ("/alertas", "/gestionar", "/versiones", "/senales"):
            self.assertNotIn(f'/portafolios/{self.portfolio_id}{link}', html, link)
        self.assertNotIn("portfolio-tabs", html)

    def test_a_hidden_portfolio_has_no_panel(self):
        self.assertEqual(self.client_for(self.novato_id).get(f"/portafolios/gestor/{self.portfolio_id}").status_code, 404)

    def test_a_liquidated_portfolio_has_no_panel(self):
        gestor = self.client_for(self.gestor_id)
        self.post(gestor, f"/portafolios/{self.portfolio_id}/visibilidad", visible="1")
        self.post(gestor, f"/portafolios/{self.portfolio_id}/archivar")
        self.assertEqual(self.client_for(self.novato_id).get(f"/portafolios/gestor/{self.portfolio_id}").status_code, 404)

    def test_a_portfolio_of_a_user_cannot_be_opened_as_shared(self):
        with self.app.app_context():
            portfolio = self._portfolio("Privado", self.novato_id)
            repositories.set_portfolio_visibility(portfolio, True)
            portfolio_id = portfolio.id
        self.assertEqual(self.client_for(self.gestor_id).get(f"/portafolios/gestor/{portfolio_id}").status_code, 404)

    def test_a_liquidated_portfolio_stops_showing(self):
        gestor = self.client_for(self.gestor_id)
        self.post(gestor, f"/portafolios/{self.portfolio_id}/visibilidad", visible="1")
        self.post(gestor, f"/portafolios/{self.portfolio_id}/archivar")
        self.assertNotIn("Ver los portafolios del gestor", self.client_for(self.novato_id).get("/").data.decode())

    def test_a_portfolio_of_another_user_is_never_shown(self):
        with self.app.app_context():
            portfolio = self._portfolio("Privado", self.novato_id)
            repositories.set_portfolio_visibility(portfolio, True)
            self.assertEqual(repositories.visible_gestor_portfolios(), [])


if __name__ == "__main__":
    unittest.main()
