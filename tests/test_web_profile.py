"""Mandatory risk profiling: questionnaire wizard, draft, result, role and "Mi perfil"."""

import json
import unittest
from urllib.parse import urlsplit

from sqlalchemy import select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import Profile, ProfileDraft
from support import TEST_PASSWORD, create_test_user
from test_web_accounts import AccountTestCase

DISCLAIMER = "Información educativa. No es recomendación de inversión ni asesoría financiera."
QUESTIONNAIRE_PATH = "/perfil/cuestionario"
# One step per page: starting point, goal, backing.
STEPS = (
    {"k1": "3", "capital_rango": "5m_30m", "aporte": "3"},
    {"horizonte": "de_3_a_5", "p2": "3", "p1": "3"},
    {"p6": "1", "decision": "2"},
)


def path_of(response) -> str:
    return urlsplit(response.location).path


class ProfileTestCase(AccountTestCase):
    def sign_in_new(self, email="nuevo@example.com", **fields):
        """Sign in (through the form) as a user without a risk profile."""
        create_test_user(self.app, email, TEST_PASSWORD, profile=False, **fields)
        response = self.post("/auth/ingresar", email=email, password=TEST_PASSWORD)
        self.refresh_csrf()
        return response

    def answer(self, step, **override):
        return self.post(QUESTIONNAIRE_PATH, paso=str(step), accion="siguiente", **{**STEPS[step], **override})

    def complete_questionnaire(self, **override):
        response = None
        for step in range(len(STEPS)):
            response = self.answer(step, **{key: value for key, value in override.items() if key in STEPS[step]})
        return response

    def profiles(self, email):
        user_id = int(self.user_id_of(email))
        with self.app.app_context():
            return db.session.execute(
                select(Profile.id, Profile.vigente, Profile.perfil_final).where(Profile.usuario_id == user_id)
                .order_by(Profile.id)
            ).all()


class MandatoryProfileTests(ProfileTestCase):
    def test_new_user_is_sent_to_the_questionnaire_on_sign_in(self):
        response = self.sign_in_new()
        self.assertEqual(path_of(response), QUESTIONNAIRE_PATH)
        html = self.client.get(QUESTIONNAIRE_PATH).data.decode()
        self.assertIn("Paso 1 de 3", html)
        self.assertIn("Menos de $50.000", html)
        self.assertIn("1. ¿Qué has hecho con tu plata hasta hoy?", html)
        self.assertIn("2. ¿Con cuánto capital cuentas hoy para invertir?", html)
        with self.client.session_transaction() as session:
            self.assertNotIn("perfil", session)

    def test_every_tool_requires_a_current_profile(self):
        self.sign_in_new()
        checked = 0
        for rule in self.app.url_map.iter_rules():
            endpoint = rule.endpoint
            if (endpoint in {"static", "main.index", "auth.login", "auth.register", "auth.logout", "auth.verify", "auth.resend_verification", "auth.forgot_password", "auth.reset_password"}
                    or endpoint.endswith(".static") or endpoint.startswith(("profile.", "onboarding.", "guia."))
                    or "GET" not in rule.methods):
                continue
            with self.app.test_request_context():
                from flask import url_for

                path = url_for(endpoint, **{argument: "1" for argument in rule.arguments})
            response = self.client.get(path)
            if path.startswith("/api/"):
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(set(json.loads(response.data)), {"error"}, path)
            else:
                self.assertEqual((response.status_code, path_of(response)), (302, QUESTIONNAIRE_PATH), path)
            checked += 1
        self.assertGreater(checked, 10)
        # The home page (portada) and the profiling pages stay reachable.
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get(QUESTIONNAIRE_PATH).status_code, 200)

    def test_user_with_a_current_profile_goes_straight_to_the_home_page(self):
        response = self.sign_in()  # fixture user: current profile, confirmed role
        self.assertEqual(path_of(response), "/")
        with self.client.session_transaction() as session:
            summary = session["perfil"]
        self.assertEqual(summary["usuario_id"], int(self.user_id_of()))
        self.assertEqual(summary["perfil_final"], "moderado")
        self.assertEqual(summary["perfil_etiqueta"], "Moderado")
        self.assertEqual(summary["factor_limitante"], "tolerancia")
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        html = self.client.get("/").data.decode()
        self.assertIn("Mi perfil", html)
        self.assertEqual(len(self.profiles("ana@example.com")), 1)

    def test_signed_out_profile_pages_show_nothing(self):
        for path in ("/perfil/", QUESTIONNAIRE_PATH, "/perfil/resultado"):
            response = self.client.get(path)
            self.assertEqual(path_of(response), "/auth/ingresar", path)
            self.assertNotIn(b"Tolerancia", response.data)


class QuestionnaireFlowTests(ProfileTestCase):
    def test_complete_questionnaire_saves_profile_and_asks_for_the_role(self):
        self.sign_in_new()
        response = self.complete_questionnaire()
        self.assertEqual(path_of(response), "/perfil/resultado")
        rows = self.profiles("nuevo@example.com")
        self.assertEqual([(vigente, final) for _id, vigente, final in rows], [(True, "moderado")])
        with self.app.app_context():
            profile = db.session.get(Profile, rows[0][0])
            self.assertEqual(profile.version_cuestionario, "2")
            self.assertEqual(profile.factor_limitante, "tolerancia")
            self.assertEqual(profile.nivel_horizonte, "arriesgado")
            self.assertEqual((profile.puntaje_tolerancia, profile.puntaje_capacidad), (6.0, 1.0))
            self.assertEqual((profile.nivel_tolerancia, profile.nivel_capacidad), ("moderado", "arriesgado"))
            self.assertEqual(profile.rol_sugerido, "inversionista")
            self.assertEqual(profile.respuestas["p1"], 3)
            self.assertEqual((profile.capital_rango, profile.horizonte), ("5m_30m", "de_3_a_5"))
            self.assertEqual(db.session.scalars(select(ProfileDraft)).all(), [])
        with self.client.session_transaction() as session:
            self.assertEqual(session["perfil"]["id"], rows[0][0])

        result = self.client.get("/perfil/resultado").data.decode()
        self.assertIn(DISCLAIMER, result)
        self.assertIn("Factor limitante: Puntaje de riesgo.", result)
        self.assertIn("6 de 9", result)
        self.assertIn("Entre 3 y 5 años", result)
        self.assertIn('value="inversionista" checked', result)
        self.assertNotIn('value="gestor"', result)

        # Profile done but the role is not confirmed yet: the tools still wait.
        self.assertEqual(path_of(self.client.get("/hrp")), "/perfil/resultado")
        response = self.post("/perfil/rol", rol="novato")
        self.assertEqual(path_of(response), "/")
        user = self.user("nuevo@example.com")
        self.assertEqual((user.role, user.role_confirmed), ("novato", True))
        self.assertEqual(self.client.get("/hrp").status_code, 200)

        # Next sign-in: no questionnaire, straight to the home page.
        self.post("/auth/salir")
        self.refresh_csrf()
        response = self.post("/auth/ingresar", email="nuevo@example.com", password=TEST_PASSWORD)
        self.assertEqual(path_of(response), "/")
        self.assertEqual(len(self.profiles("nuevo@example.com")), 1)

    def test_veto_answer_forces_adverse_and_is_explained(self):
        self.sign_in_new()
        self.complete_questionnaire(p6="3", horizonte="mas_5")
        rows = self.profiles("nuevo@example.com")
        self.assertEqual(rows[0][2], "adverso")
        html = self.client.get("/perfil/resultado").data.decode()
        self.assertIn("Tu perfil: Conservador", html)
        self.assertIn("Factor limitante: Respuesta determinante.", html)
        self.assertIn("Todavía no tienes un fondo de emergencia", html)

    def test_partial_progress_is_resumed_on_the_next_sign_in(self):
        self.sign_in_new()
        self.answer(0)
        self.answer(1, p2="4")
        self.post(QUESTIONNAIRE_PATH, paso="2", accion="siguiente", p6="2")  # incomplete step
        with self.app.app_context():
            draft = db.session.scalars(select(ProfileDraft)).one()
            self.assertEqual(draft.paso, 2)
            self.assertEqual(draft.datos["capital_rango"], "5m_30m")
            self.assertEqual(draft.datos["respuestas"]["p2"], 4)
            self.assertEqual(draft.datos["respuestas"]["p6"], 2)
        self.post("/auth/salir")
        self.refresh_csrf()
        response = self.post("/auth/ingresar", email="nuevo@example.com", password=TEST_PASSWORD)
        self.assertEqual(path_of(response), QUESTIONNAIRE_PATH)
        html = self.client.get(QUESTIONNAIRE_PATH).data.decode()
        self.assertIn("Paso 3 de 3", html)
        self.assertIn('name="p6" value="2" checked', html)
        previous = self.client.get(QUESTIONNAIRE_PATH + "?paso=1").data.decode()
        self.assertIn('name="p2" value="4" checked', previous)
        # Steps not reached yet cannot be opened.
        self.assertIn("Paso 3 de 3", self.client.get(QUESTIONNAIRE_PATH + "?paso=3").data.decode())
        self.refresh_csrf()
        response = self.answer(2)
        self.assertEqual(path_of(response), "/perfil/resultado")
        self.assertEqual(len(self.profiles("nuevo@example.com")), 1)

    def test_incomplete_or_invalid_step_is_rejected(self):
        self.sign_in_new()
        response = self.post(QUESTIONNAIRE_PATH, paso="0", accion="siguiente", k1="3", capital_rango="1000")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Responde todas las preguntas", response.data.decode())
        self.answer(0)
        response = self.answer(1, p1="5")
        self.assertEqual(response.status_code, 400)
        # Jumping ahead to an unreached step does nothing.
        response = self.answer(2)
        self.assertEqual(path_of(response), QUESTIONNAIRE_PATH)
        self.assertEqual(self.profiles("nuevo@example.com"), [])

    def test_previous_button_keeps_answers(self):
        self.sign_in_new()
        self.answer(0)
        response = self.post(QUESTIONNAIRE_PATH, paso="1", accion="anterior", p1="2")
        self.assertEqual(urlsplit(response.location).query, "paso=0")
        html = self.client.get(QUESTIONNAIRE_PATH + "?paso=1").data.decode()
        self.assertIn('name="p1" value="2" checked', html)


class RepeatQuestionnaireTests(ProfileTestCase):
    def test_repeating_adds_a_row_and_keeps_one_current(self):
        self.sign_in()
        mine = self.client.get("/perfil/").data.decode()
        self.assertIn(DISCLAIMER, mine)
        self.assertIn("Factor limitante: Puntaje de riesgo.", mine)
        self.assertIn("queda guardado", mine)
        self.assertIn("Repetir cuestionario", mine)
        first = self.profiles("ana@example.com")
        self.assertEqual(len(first), 1)

        response = self.post("/perfil/repetir")
        self.assertEqual(path_of(response), QUESTIONNAIRE_PATH)
        html = self.client.get(QUESTIONNAIRE_PATH).data.decode()
        self.assertIn("Estás repitiendo el cuestionario", html)
        # While repeating, the current profile still opens the tools.
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.complete_questionnaire(horizonte="menos_1")
        rows = self.profiles("ana@example.com")
        self.assertEqual(len(rows), 2)
        self.assertEqual([vigente for _id, vigente, _final in rows], [False, True])
        self.assertEqual(rows[0][0], first[0][0])  # the old row is kept, not overwritten
        self.assertEqual(rows[1][2], "adverso")
        with self.client.session_transaction() as session:
            self.assertEqual(session["perfil"]["id"], rows[1][0])
            self.assertEqual(session["perfil"]["perfil_final"], "adverso")
        mine = self.client.get("/perfil/").data.decode()
        self.assertIn("Historial", mine)
        self.assertIn("Anterior", mine)

        self.post("/perfil/repetir")
        self.complete_questionnaire()
        with self.app.app_context():
            current = db.session.scalars(select(Profile).where(Profile.vigente.is_(True))).all()
        self.assertEqual(len(current), 1)
        self.assertEqual(len(self.profiles("ana@example.com")), 3)


class VersionOneProfileTests(ProfileTestCase):
    def legacy_profile(self, email):
        """Turn the user's profile into one saved with version 1 of the questionnaire."""
        user_id = int(self.user_id_of(email))
        with self.app.app_context():
            profile = db.session.scalars(select(Profile).where(Profile.usuario_id == user_id)).one()
            profile.version_cuestionario = "1"
            profile.capital_rango = "20m_100m"
            profile.horizonte = "mas_3"
            profile.respuestas = {"p1": 3, "p6": 2, "p7": 2, "p8": 3, "k1": 3, "k2": 2}
            profile.puntaje_tolerancia, profile.puntaje_capacidad = 2.4, 2.33
            profile.factor_limitante = "capacidad"
            db.session.commit()

    def test_a_saved_profile_is_kept_and_stays_readable(self):
        self.sign_in()
        self.legacy_profile("ana@example.com")
        self.assertEqual(self.client.get("/hrp").status_code, 200)  # nothing to repeat
        for path in ("/perfil/", "/perfil/resultado"):
            html = self.client.get(path).data.decode()
            self.assertIn("De 20 a 100 millones de pesos", html)
            self.assertIn("Más de 3 años", html)
            self.assertIn("2,40 de 4", html)
            self.assertIn("Capacidad de asumir pérdidas", html)
        self.assertEqual(len(self.profiles("ana@example.com")), 1)

    def test_a_draft_of_version_1_is_discarded(self):
        self.sign_in_new()
        user_id = int(self.user_id_of("nuevo@example.com"))
        with self.app.app_context():
            db.session.add(ProfileDraft(
                usuario_id=user_id, version_cuestionario="1", paso=2,
                datos={"capital_rango": "5m_20m", "respuestas": {"p3": 3}},
            ))
            db.session.commit()
        self.assertIn("Paso 1 de 3", self.client.get(QUESTIONNAIRE_PATH).data.decode())


class RoleConfirmationTests(ProfileTestCase):

    def test_nobody_can_choose_gestor(self):
        self.sign_in_new()
        self.complete_questionnaire()
        response = self.post("/perfil/rol", rol="gestor")
        self.assertEqual(path_of(response), "/perfil/resultado")
        user = self.user("nuevo@example.com")
        self.assertEqual((user.role, user.role_confirmed), ("novato", False))
        self.post("/perfil/rol", rol="inversionista")
        user = self.user("nuevo@example.com")
        self.assertEqual((user.role, user.role_confirmed), ("inversionista", True))
        self.post("/perfil/rol", rol="gestor")
        self.assertEqual(self.user("nuevo@example.com").role, "inversionista")

    def test_gestor_skips_the_questionnaire(self):
        """The gestor uses the original app: no questionnaire, no role step."""
        response = self.sign_in_new("jefa@example.com", role="gestor", role_confirmed=True)
        self.assertNotEqual(path_of(response), QUESTIONNAIRE_PATH)
        self.assertEqual(self.user("jefa@example.com").role, "gestor")
        self.assertEqual(self.client.get("/hrp").status_code, 200)
        self.post("/perfil/rol", rol="novato")
        user = self.user("jefa@example.com")
        self.assertEqual((user.role, user.role_confirmed), ("gestor", True))
        self.assertEqual(self.client.get("/hrp").status_code, 200)

    def test_former_gestor_must_confirm_a_role(self):
        create_test_user(self.app, "ex@example.com", role="gestor", role_confirmed=True)  # profiled
        result = self.app.test_cli_runner().invoke(args=["users", "revoke-gestor", "ex@example.com"])
        self.assertEqual(result.exit_code, 0, result.output)
        response = self.post("/auth/ingresar", email="ex@example.com", password=TEST_PASSWORD)
        self.assertEqual(path_of(response), "/perfil/resultado")


if __name__ == "__main__":
    unittest.main()
