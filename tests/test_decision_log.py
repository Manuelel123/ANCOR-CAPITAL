"""Decision log: one append-only row per generated portfolio, with the current profile."""

import re
import unittest
from unittest.mock import patch

from sqlalchemy import select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.decisions import list_decisions, record_decision
from optimizacion_portafolios.storage.models import AppendOnlyError, DecisionRecord
from optimizacion_portafolios.storage.profiles import get_current_profile
from optimizacion_portafolios.web.common import guided
from support import add_test_profile
from test_tracking import _prices
from test_web_roles import RoleTestCase, priced

HRP_PRICES = "optimizacion_portafolios.web.hrp.services.download_prices"
COMPARE_PRICES = "optimizacion_portafolios.web.compare.services.download_prices"
PORTFOLIO_PRICES = "optimizacion_portafolios.web.portfolios.services.download_prices"


class DecisionLogMixin:
    def records(self) -> list[DecisionRecord]:
        with self.app.app_context():
            rows = db.session.scalars(select(DecisionRecord).order_by(DecisionRecord.id)).all()
            for row in rows:
                db.session.expunge(row)
            return rows

    def current_profile_id(self) -> int:
        with self.app.app_context():
            return get_current_profile(self.user_id).id


class NovatoDecisionTests(DecisionLogMixin, RoleTestCase):
    role = "novato"

    @patch(HRP_PRICES, side_effect=priced)
    def test_guided_hrp_inserts_one_row_with_the_current_profile(self, _download):
        self.post("/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "GOOGL", "BTC-USD"], min_weight="40")
        (row,) = self.records()
        self.assertEqual(row.usuario_id, int(self.user_id))
        self.assertEqual((row.metodo, row.origen, row.rol), ("hrp", "hrp", "novato"))
        self.assertEqual(row.perfil_id, self.current_profile_id())
        self.assertEqual(row.perfil_final, "moderado")
        self.assertEqual(set(row.activos), {"AAPL", "MSFT", "GOOGL", "BTC-USD"})
        self.assertAlmostEqual(sum(row.activos.values()), 1.0, places=6)
        self.assertEqual(row.parametros["mode"], "guiada")
        self.assertEqual(row.parametros["universe"], "Acciones y Bitcoin")
        self.assertEqual(row.parametros["start_date"], guided.guided_start().isoformat())
        self.assertEqual(row.parametros["max_weight"], 1.0)
        self.assertIsNotNone(row.fecha)


    @patch(HRP_PRICES, side_effect=priced)
    def test_new_profile_is_used_by_later_rows(self, _download):
        self.post("/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "GOOGL", "BTC-USD"])
        first_profile = self.current_profile_id()
        new_profile = add_test_profile(self.app, self.user_id, horizon="menos_1")
        with self.client.session_transaction() as session:
            session.pop("perfil", None)  # as after repeating the questionnaire
        self.post("/hrp", action="optimize", universe="Sectores de EE. UU.", tickers=["XLV", "XLF", "XLK", "XLE"])
        first, second = self.records()
        self.assertEqual((first.perfil_id, second.perfil_id), (first_profile, new_profile))
        self.assertEqual(second.perfil_final, "adverso")

    @patch(HRP_PRICES, side_effect=ValueError("sin datos"))
    def test_failed_calculation_records_nothing(self, _download):
        self.post("/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "GOOGL", "BTC-USD"])
        self.assertEqual(self.records(), [])

    @patch(HRP_PRICES, side_effect=priced)
    def test_history_page_lists_only_own_rows(self, _download):
        self.post("/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "GOOGL", "BTC-USD"])
        with self.app.app_context():
            from support import create_test_user

            other = create_test_user(self.app, "otra@example.com")
            record_decision(other, method="hrp", source="hrp", weights={"ZZZZ": 1.0}, role="novato")
        html = self.client.get("/historial").data.decode()
        self.assertIn("Mi historial de decisiones", html)
        self.assertEqual(len(re.findall(r"<tr data-decision=", html)), 1)
        self.assertIn("Moderado", html)
        self.assertIn("BTC-USD", html)
        self.assertNotIn("ZZZZ", html)
        self.assertIn("no se puede editar ni borrar", html)

    def test_rows_cannot_be_changed_or_deleted(self):
        with self.app.app_context():
            row = record_decision(self.user_id, method="hrp", source="hrp", weights={"AAPL": 1.0}, role="novato")
            row.activos = {"AAPL": 0.5}
            with self.assertRaises(AppendOnlyError):
                db.session.commit()
            db.session.rollback()
            db.session.delete(db.session.get(DecisionRecord, row.id))
            with self.assertRaises(AppendOnlyError):
                db.session.commit()
            db.session.rollback()
            self.assertEqual([r.activos for r in list_decisions(self.user_id)], [{"AAPL": 1.0}])
        with self.assertRaises(ValueError):
            with self.app.app_context():
                record_decision(self.user_id, method="otro", source="hrp", weights={"A": 1.0}, role="x")


class InversionistaDecisionTests(DecisionLogMixin, RoleTestCase):
    role = "inversionista"


    @patch(PORTFOLIO_PRICES, side_effect=_prices)
    @patch(HRP_PRICES, side_effect=_prices)
    def test_fixing_a_portfolio_is_marked(self, *_mocks):
        html = self.post("/hrp", action="optimize", universe="Acciones y Bitcoin", tickers=["AAPL", "MSFT", "GOOGL"]).data.decode()
        token = re.search(r'name="snapshot" value="([^"]+)"', html).group(1)
        response = self.post("/portafolios/fijar", snapshot=token, step="confirm", name="Mi guiado",
                             capital="100000", commission_bps="10", benchmark="SPY")
        self.assertEqual(response.status_code, 302)
        generated, fixed = self.records()
        self.assertEqual((generated.origen, fixed.origen, fixed.metodo), ("hrp", "fijar", "hrp"))
        self.assertEqual(fixed.activos, generated.activos)
        self.assertEqual(fixed.parametros["name"], "Mi guiado")
        self.assertIn("portfolio_id", fixed.parametros)


class GestorDecisionTests(DecisionLogMixin, RoleTestCase):
    role = "gestor"

    @patch(HRP_PRICES, side_effect=priced)
    def test_full_hrp_page_stores_its_parameters(self, _download):
        self.post("/hrp", action="optimize", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
                  start_date="2024-01-01", tickers=["AAPL", "MSFT", "XLV"], custom_tickers="",
                  min_weight="10", max_weight="60")
        (row,) = self.records()
        self.assertEqual((row.rol, row.parametros["mode"]), ("gestor", "completa"))
        self.assertEqual(row.parametros["start_date"], "2024-01-01")
        self.assertEqual(row.parametros["bounds"]["min_weight"], 0.1)
        self.assertFalse(row.parametros["backtest"])

    @patch(HRP_PRICES, side_effect=priced)
    def test_report_download_is_not_a_decision(self, _download):
        self.post("/hrp", action="download_report", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
                  start_date="2024-01-01", tickers=["AAPL", "MSFT"], custom_tickers="")
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
