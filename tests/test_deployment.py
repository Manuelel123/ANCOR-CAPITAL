"""Settings for hosted deployments (Render): database URL and reverse proxy."""

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import request
from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from optimizacion_portafolios.storage import MIGRATIONS_DIRECTORY, db
from optimizacion_portafolios.storage.models import (
    DecisionRecord,
    Holding,
    Portfolio,
    Transaction,
    User,
)
from optimizacion_portafolios.web import create_app, database_url


class DatabaseUrlTests(unittest.TestCase):
    def test_postgres_urls_use_the_psycopg_driver(self):
        for url in ("postgres://u:p@host/db", "postgresql://u:p@host/db"):
            with self.subTest(url=url):
                self.assertEqual(database_url(url), "postgresql+psycopg://u:p@host/db")

    def test_other_urls_are_kept(self):
        self.assertEqual(database_url("sqlite:///instance/atlas.db"), "sqlite:///instance/atlas.db")
        self.assertEqual(database_url("postgresql+psycopg://h/db"), "postgresql+psycopg://h/db")
        self.assertIsNone(database_url(""))
        self.assertIsNone(database_url(None))


class ProxyTests(unittest.TestCase):
    def _client_addr(self, hops):
        with patch.dict(os.environ, {"ATLAS_PROXY_HOPS": hops}):
            app = create_app({"TESTING": True, "SECRET_KEY": "x"})

        # Answer before the sign-in guard: only the request's view of the client matters.
        app.before_request_funcs[None].insert(0, lambda: f"{request.remote_addr} {request.scheme}")
        response = app.test_client().get(
            "/",
            headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
        )
        return response.get_data(as_text=True)

    def test_trusted_proxy_hop_gives_the_client_ip_and_scheme(self):
        self.assertEqual(self._client_addr("1"), "203.0.113.7 https")

    def test_forwarded_headers_are_ignored_without_trusted_proxies(self):
        self.assertEqual(self._client_addr(""), "10.0.0.1 http")


class ResilienceTests(unittest.TestCase):
    def test_pooled_connections_are_checked_before_use(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        with app.app_context():
            self.assertTrue(db.engine.pool._pre_ping)

    def test_unexpected_errors_get_the_app_error_page(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "x", "PROPAGATE_EXCEPTIONS": False})

        def fail():
            raise RuntimeError("boom")

        app.before_request_funcs[None].insert(0, fail)
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Ocurrió un error inesperado".encode(), response.data)


class MigrationLoggingTests(unittest.TestCase):
    def test_running_migrations_keeps_the_app_loggers_enabled(self):
        # Start-up runs the migrations after the app modules exist; their
        # errors (e.g. a rejected email) must still reach the server log.
        mail_logger = logging.getLogger("optimizacion_portafolios.web.auth.mailer")
        with tempfile.TemporaryDirectory() as folder:
            uri = "sqlite:///" + os.path.join(folder, "atlas.db")
            app = create_app({"TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": uri})
            with app.app_context():
                db.drop_all()  # tests create the tables; the migrations need an empty database
                upgrade(directory=MIGRATIONS_DIRECTORY)
                db.engine.dispose()
        self.assertFalse(mail_logger.disabled)


class GestorPortfolioMigrationTests(unittest.TestCase):
    PREVIOUS = "b8938e7ff6a1"
    NAME = "HRP · 16 activos · 2026-11-01"

    def _migrate(self, roles):
        """Portfolios after the migration, with one user per role in ``roles``."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            uri = "sqlite:///" + os.path.join(folder, "atlas.db")
            app = create_app({"TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": uri})
            with app.app_context():
                db.drop_all()
                upgrade(directory=MIGRATIONS_DIRECTORY, revision=self.PREVIOUS)
                # Plain SQL: the model may already have columns of later migrations.
                for index, role in enumerate(roles):
                    db.session.execute(
                        text(
                            "INSERT INTO users (email, password_hash, role, role_confirmed, plan, created_at)"
                            " VALUES (:email, 'x', :role, false, 'free', CURRENT_TIMESTAMP)"
                        ),
                        {"email": f"{index}@test.local", "role": role},
                    )
                db.session.commit()
                upgrade(directory=MIGRATIONS_DIRECTORY)
                result = self._summary()
                # Running it again must not duplicate the portfolio.
                downgrade(directory=MIGRATIONS_DIRECTORY, revision=self.PREVIOUS)
                upgrade(directory=MIGRATIONS_DIRECTORY)
                result["after_rerun"] = Portfolio.query.count()
                db.session.remove()
                db.engine.dispose()
        return result

    def _summary(self):
        portfolios = Portfolio.query.all()
        if not portfolios:
            return {"portfolios": 0}
        portfolio = portfolios[0]
        version = portfolio.versions[0]
        return {
            "portfolios": len(portfolios),
            "name": portfolio.name,
            "owner": portfolio.user_id,
            "gestor": str(User.query.filter_by(role="gestor").one().id),
            "holdings": Holding.query.filter_by(version_id=version.id).count(),
            "weight": sum(h.target_weight for h in Holding.query.filter_by(version_id=version.id)),
            "transactions": Transaction.query.filter_by(version_id=version.id).count(),
            "snapshots": portfolio.snapshots.count(),
            "rules": len(portfolio.alert_rules),
            "decision_portfolio": DecisionRecord.query.one().parametros["portfolio_id"],
            "portfolio_id": portfolio.id,
        }

    def test_the_only_gestor_receives_the_whole_portfolio_once(self):
        result = self._migrate(["novato", "gestor"])
        self.assertEqual(result["portfolios"], 1)
        self.assertEqual(result["after_rerun"], 1)
        self.assertEqual(result["name"], self.NAME)
        self.assertEqual(result["owner"], result["gestor"])
        self.assertEqual((result["holdings"], result["transactions"]), (16, 16))
        self.assertAlmostEqual(result["weight"], 1.0)
        self.assertEqual((result["snapshots"], result["rules"]), (4, 7))
        self.assertEqual(result["decision_portfolio"], result["portfolio_id"])

    def test_nothing_is_added_without_exactly_one_gestor(self):
        for roles in ([], ["novato"], ["gestor", "gestor"]):
            with self.subTest(roles=roles):
                result = self._migrate(roles)
                self.assertEqual(result["portfolios"], 0)
                self.assertEqual(result["after_rerun"], 0)


if __name__ == "__main__":
    unittest.main()
