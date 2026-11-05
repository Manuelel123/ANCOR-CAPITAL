"""First steps of a user: data notice, introduction and the broker guide."""

import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import yaml

import optimizacion_portafolios
from optimizacion_portafolios.profiling.avisos import DEFAULT_PATH, NoticesError, load_notices, parse_notices
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.users import get_user_by_email
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.guia.content import DEFAULT_PATH as GUIDE_FILE
from optimizacion_portafolios.web.guia.content import GuideError, load_guide, parse_guide
from support import TEST_PASSWORD, add_test_profile, create_test_user, csrf_from, login_session
from test_web_accounts import AccountTestCase

DISCLAIMER = "Información educativa. No es recomendación de inversión ni asesoría financiera."
NOTICE_PATH = "/aviso-datos"
INTRO_PATH = "/introduccion"
GUIDE_PATH = "/guia/interactive-brokers"


def path_of(response) -> str:
    return urlsplit(response.location).path


class OnboardingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()

    def sign_in(self, email="nueva@example.com", **fields):
        user_id = create_test_user(self.app, email, TEST_PASSWORD, **fields)
        login_session(self.client, user_id)
        return user_id

    def post(self, path, **data):
        data["csrf_token"] = csrf_from(self.client.get(path, follow_redirects=True))
        return self.client.post(path, data=data)

    def user(self, email="nueva@example.com"):
        return get_user_by_email(email)


class DataNoticeTests(OnboardingTestCase):
    def test_tools_redirect_to_the_notice_until_it_is_accepted(self):
        self.sign_in(notice=False)
        for path in ("/hrp", "/perfil/cuestionario", "/perfil/", "/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(path_of(response), NOTICE_PATH)

    def test_the_gestor_does_not_get_the_notice(self):
        self.sign_in(notice=False, intro=False, role="gestor", role_confirmed=True)
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_public_pages_stay_open_without_a_session(self):
        for path in ("/", "/auth/ingresar", "/auth/registro"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_sign_out_is_not_blocked(self):
        self.sign_in(notice=False)
        response = self.post(NOTICE_PATH)  # token only, the notice stays pending
        self.assertEqual(response.status_code, 200)
        token = csrf_from(response)
        logout = self.client.post("/auth/salir", data={"csrf_token": token})
        self.assertEqual(logout.status_code, 302)
        self.assertNotEqual(path_of(logout), NOTICE_PATH)

    def test_the_notice_shows_the_three_uses_and_is_not_preselected(self):
        self.sign_in(notice=False)
        html = self.client.get(NOTICE_PATH).get_data(as_text=True)
        for use in load_notices().uses:
            self.assertIn(use, html)
        self.assertEqual(html.count('type="checkbox"'), 1)
        self.assertNotIn("checked", html)
        self.assertIn(DISCLAIMER, html)
        self.assertIn(load_notices().legal_notice, html)
        self.assertIn("pendiente de revisión legal", html)

    def test_an_empty_checkbox_does_not_advance(self):
        self.sign_in(notice=False)
        response = self.post(NOTICE_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("marca la casilla", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(self.user().datos_aceptados_at)

    def test_accepting_stores_date_and_version(self):
        self.sign_in(notice=False)
        response = self.post(NOTICE_PATH, acepto="1")
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(path_of(response), NOTICE_PATH)
        with self.app.app_context():
            user = self.user()
            self.assertIsNotNone(user.datos_aceptados_at)
            self.assertEqual(user.datos_aviso_version, load_notices().version)
        self.assertEqual(self.client.get("/hrp").status_code, 200)

    def test_a_new_notice_version_asks_again(self):
        self.sign_in()
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        with self.app.app_context():
            self.user().datos_aviso_version = "0"
            db.session.commit()
        self.assertEqual(path_of(self.client.get("/hrp")), NOTICE_PATH)

    def test_an_existing_user_keeps_the_profile_after_accepting(self):
        self.sign_in(notice=False, intro=False)
        response = self.post(NOTICE_PATH, acepto="1")
        self.assertEqual(path_of(response), INTRO_PATH)
        self.client.get(INTRO_PATH)
        self.assertEqual(self.client.get("/perfil/").status_code, 200)
        self.assertEqual(self.client.get("/hrp").status_code, 200)


class IntroductionTests(OnboardingTestCase):
    def test_it_opens_by_itself_only_the_first_time(self):
        self.sign_in(intro=False)
        for path in ("/hrp", "/"):
            self.assertEqual(path_of(self.client.get(path)), INTRO_PATH, path)
        html = self.client.get(INTRO_PATH).get_data(as_text=True)
        self.assertIn(DISCLAIMER, html)
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_it_can_be_reopened_from_the_user_menu(self):
        self.sign_in()
        self.assertIn(f'href="{INTRO_PATH}"', self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get(INTRO_PATH).status_code, 200)

    def test_it_needs_the_data_notice_first(self):
        self.sign_in(notice=False, intro=False)
        self.assertEqual(path_of(self.client.get(INTRO_PATH)), NOTICE_PATH)

    def test_a_new_user_goes_on_to_the_questionnaire(self):
        self.sign_in(profile=False, notice=False, intro=False)
        self.assertEqual(path_of(self.post(NOTICE_PATH, acepto="1")), INTRO_PATH)
        html = self.client.get(INTRO_PATH).get_data(as_text=True)
        self.assertIn('href="/perfil/cuestionario"', html)
        self.assertEqual(self.client.get("/perfil/cuestionario").status_code, 200)

    def test_it_explains_both_routes_with_the_first_question_options(self):
        self.sign_in()
        html = self.client.get(INTRO_PATH).get_data(as_text=True)
        for text in ("Tu perfil", "Opciones explicadas", "Guía hasta tu primera inversión", "Seguimiento",
                     "Ruta A", "Ruta B", "natillera"):
            self.assertIn(text, html)

    def test_the_gestor_does_not_get_it_automatically(self):
        self.sign_in(role="gestor", role_confirmed=True, intro=False)
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        html = self.client.get(INTRO_PATH).get_data(as_text=True)
        self.assertIn(DISCLAIMER, html)


class FullJourneyTests(AccountTestCase):
    def test_a_new_account_walks_the_whole_path(self):
        self.register(email="nueva@example.com", name="Nueva")
        code = self.last_code("nueva@example.com")
        response = self.post("/auth/verificar", code=code)
        self.refresh_csrf()
        self.assertEqual(path_of(response), NOTICE_PATH)

        response = self.post(NOTICE_PATH, acepto="1")
        self.assertEqual(path_of(response), INTRO_PATH)
        self.assertIn("/perfil/cuestionario", self.client.get(INTRO_PATH).get_data(as_text=True))

        steps = (
            {"k1": "1", "capital_rango": "menos_50k", "aporte": "2"},
            {"horizonte": "mas_5", "p2": "3", "p1": "3"},
            {"p6": "1", "decision": "2"},
        )
        for number, answers in enumerate(steps):
            response = self.post("/perfil/cuestionario", paso=str(number), accion="siguiente", **answers)
        self.assertEqual(path_of(response), "/perfil/resultado")
        self.assertEqual(path_of(self.client.get("/hrp")), "/perfil/resultado")

        result = self.client.get("/perfil/resultado").get_data(as_text=True)
        self.assertIn(GUIDE_PATH, result)
        self.assertEqual(path_of(self.post("/perfil/rol", rol="novato")), "/")

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.assertIn("no es el camino principal", self.client.get(GUIDE_PATH).get_data(as_text=True))


class GuideTests(OnboardingTestCase):
    LOW_NOTE = "no es el camino principal"
    LOW_EXAMPLE = "FIC600 de Banco Agrario"
    MEDIUM_NOTE = "es el camino para acciones y ETF"
    COMMISSION = "Atlas puede recibir una comisión"

    def guide_for(self, capital, email="guia@example.com"):
        user_id = self.sign_in(email, profile=False, role_confirmed=True)
        add_test_profile(self.app, user_id, capital=capital)
        return self.client.get(GUIDE_PATH).get_data(as_text=True)

    def test_it_opens_for_the_three_roles(self):
        for role in ("novato", "inversionista", "gestor"):
            with self.subTest(role=role):
                self.client = self.app.test_client()
                self.sign_in(f"{role}@example.com", role=role, role_confirmed=True)
                response = self.client.get(GUIDE_PATH)
                self.assertEqual(response.status_code, 200)
                self.assertIn(DISCLAIMER, response.get_data(as_text=True))

    def test_without_a_session_it_goes_to_sign_in(self):
        response = self.client.get(GUIDE_PATH)
        self.assertEqual(path_of(response), "/auth/ingresar")

    def test_it_needs_the_data_notice(self):
        self.sign_in(notice=False)
        self.assertEqual(path_of(self.client.get(GUIDE_PATH)), NOTICE_PATH)

    def test_it_opens_from_the_result_page_before_the_role_is_confirmed(self):
        user_id = self.sign_in(profile=False)
        add_test_profile(self.app, user_id)
        self.assertIn(GUIDE_PATH, self.client.get("/perfil/resultado").get_data(as_text=True))
        self.assertEqual(self.client.get(GUIDE_PATH).status_code, 200)

    def test_it_is_linked_from_the_menu_and_the_home_page(self):
        self.sign_in()
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f'href="{GUIDE_PATH}"', html)
        self.assertIn('data-module="guide"', html)

    def test_low_capital_changes_the_message_but_not_the_guide(self):
        for capital in ("menos_50k", "50k_200k", "200k_1m", "1m_5m"):
            with self.subTest(capital=capital):
                self.client = self.app.test_client()
                html = self.guide_for(capital, f"{capital}@example.com")
                self.assertIn(self.LOW_NOTE, html)
                self.assertIn(self.LOW_EXAMPLE, html)
                self.assertIn("Verifica con la entidad", html)
                self.assertNotIn(self.MEDIUM_NOTE, html)
                self.assertIn("Primera compra", html)

    def test_medium_and_high_capital_do_not_get_the_low_capital_note(self):
        for capital in ("5m_30m", "mas_30m"):
            with self.subTest(capital=capital):
                self.client = self.app.test_client()
                html = self.guide_for(capital, f"{capital}@example.com")
                self.assertNotIn(self.LOW_NOTE, html)
                self.assertNotIn(self.LOW_EXAMPLE, html)
                self.assertIn(self.MEDIUM_NOTE, html)

    def test_it_always_shows_criteria_agreement_alternative_and_open_questions(self):
        html = self.guide_for("mas_30m")
        guide = load_guide()
        for text in (guide["referencia"], "SEC y FINRA", "Sin depósito mínimo", "más de 150 mercados",
                     guide["estado_referido"]["sin_acuerdo"], "Trii", "Por confirmar", "por transferencia",
                     "Verificado el 2026-11-04", "verifica en IBKR"):
            self.assertIn(text, html)
        for source in guide["fuentes"]:
            self.assertIn(source["url"], html)
        for step in guide["pasos"]:
            self.assertIn(step["por_que"], html)

    def test_the_link_is_the_official_one_without_a_referral_code(self):
        self.assertIn('href="https://www.interactivebrokers.com"', self.guide_for("mas_30m"))

    def test_no_commission_notice_while_there_is_no_agreement(self):
        html = self.guide_for("mas_30m")
        self.assertNotIn(self.COMMISSION, html)
        self.assertNotIn("acuerdo de referido", html)

    def test_the_commission_notice_shows_only_with_an_agreement(self):
        data = yaml.safe_load(GUIDE_FILE.read_text(encoding="utf-8"))
        data["referido_ibkr"] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "guia.yaml"
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            self.app = create_app({"TESTING": True, "SECRET_KEY": "x", "GUIA_IBKR_PATH": str(path)})
            self.client = self.app.test_client()
            html = self.guide_for("mas_30m")
        self.assertIn(self.COMMISSION, html)
        self.assertIn("acuerdo de referido", html)


class GuideFileTests(unittest.TestCase):
    def data(self):
        return yaml.safe_load(GUIDE_FILE.read_text(encoding="utf-8"))

    def test_the_shipped_file_is_valid_and_has_no_agreement(self):
        guide = load_guide()
        self.assertIs(guide["referido_ibkr"], False)
        self.assertEqual(len(guide["pasos"]), 8)
        self.assertEqual(len(guide["por_confirmar"]["puntos"]), 4)

    def test_a_missing_field_names_it(self):
        data = self.data()
        del data["pasos"][2]["por_que"]
        with self.assertRaisesRegex(GuideError, "por_que.*pasos.*3"):
            parse_guide(data)
        data = self.data()
        del data["capital"]["bajo"]["nota"]
        with self.assertRaisesRegex(GuideError, "capital.bajo.nota"):
            parse_guide(data)

    def test_the_flag_must_be_a_boolean(self):
        data = self.data()
        data["referido_ibkr"] = "no"
        with self.assertRaisesRegex(GuideError, "referido_ibkr"):
            parse_guide(data)

    def test_a_referral_link_is_rejected(self):
        data = self.data()
        data["enlace_oficial"] = "https://www.interactivebrokers.com/?ref=atlas"
        with self.assertRaisesRegex(GuideError, "enlace_oficial"):
            parse_guide(data)

    def test_the_app_does_not_start_with_an_invalid_file(self):
        data = self.data()
        del data["fuentes"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "guia.yaml"
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(GuideError, "fuentes"):
                create_app({"TESTING": True, "SECRET_KEY": "x", "GUIA_IBKR_PATH": str(path)})


class LanguageTests(unittest.TestCase):
    def test_new_texts_do_not_recommend(self):
        web = Path(optimizacion_portafolios.__file__).parent / "web"
        files = [DEFAULT_PATH, GUIDE_FILE, *(web / "onboarding").rglob("*.html"), *(web / "guia").rglob("*.html")]
        self.assertGreaterEqual(len(files), 5)
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in ("recomendamos", "te recomendamos", "invierte aquí"):
                self.assertNotIn(forbidden, text, path.name)


class ProfileRulesTests(OnboardingTestCase):
    TOOLS = ("/hrp", "/black-litterman", "/portafolios/", "/historial")
    ANSWERS = {"k1": 1, "aporte": 1, "p2": 4, "p1": 4, "p6": 1, "decision": 1}

    def profiled(self, capital="1m_5m", horizon="mas_5", **answers):
        user_id = self.sign_in(profile=False, role_confirmed=True)
        add_test_profile(self.app, user_id, capital=capital, horizon=horizon, answers={**self.ANSWERS, **answers})
        return self.client.get("/perfil/resultado").get_data(as_text=True)

    def test_without_an_emergency_fund_the_profile_is_conservative_and_nothing_is_blocked(self):
        for option, email in ((2, "parcial@example.com"), (3, "sin@example.com")):
            with self.subTest(option=option):
                self.client = self.app.test_client()
                user_id = self.sign_in(email, profile=False, role_confirmed=True)
                add_test_profile(self.app, user_id, horizon="mas_5", capital="1m_5m",
                                 answers={**self.ANSWERS, "p6": option})
                self.assertIn("Tu perfil: Conservador", self.client.get("/perfil/resultado").get_data(as_text=True))
                for path in self.TOOLS:
                    self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_needing_the_money_within_a_year_is_conservative_and_nothing_is_blocked(self):
        self.assertIn("Tu perfil: Conservador", self.profiled(horizon="menos_1"))
        for path in self.TOOLS:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_lowest_capital_gets_the_same_result_as_the_highest(self):
        html = {}
        for capital in ("menos_50k", "mas_30m"):
            self.client = self.app.test_client()
            user_id = self.sign_in(f"{capital}@example.com", profile=False, role_confirmed=True)
            add_test_profile(self.app, user_id, capital=capital, horizon="mas_5", answers=self.ANSWERS)
            html[capital] = self.client.get("/perfil/resultado").get_data(as_text=True)
            for path in self.TOOLS:
                self.assertEqual(self.client.get(path).status_code, 200, path)
        for page in html.values():
            self.assertIn("Tu perfil: Arriesgado", page)


class NoticesFileTests(unittest.TestCase):
    def data(self):
        return yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))

    def test_the_shipped_file_is_valid_and_marked_as_a_draft(self):
        notice = load_notices()
        self.assertEqual(len(notice.uses), 3)
        self.assertTrue(notice.pending_review)
        self.assertIn("PENDIENTE: revisión legal (Ley 1581 de 2012)", DEFAULT_PATH.read_text(encoding="utf-8"))

    def test_a_missing_field_names_it(self):
        data = self.data()
        del data["aviso_datos"]["casilla"]
        with self.assertRaisesRegex(NoticesError, "aviso_datos.casilla"):
            parse_notices(data)

    def test_every_use_must_be_listed(self):
        data = self.data()
        data["aviso_datos"]["usos"].pop()
        with self.assertRaisesRegex(NoticesError, "usos"):
            parse_notices(data)

    def test_the_app_does_not_start_with_an_invalid_file(self):
        data = self.data()
        del data["version"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "avisos.yaml"
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(NoticesError, "version"):
                create_app({"TESTING": True, "SECRET_KEY": "x", "AVISOS_PATH": str(path)})


if __name__ == "__main__":
    unittest.main()
