"""Risk-profile questionnaire: YAML loading/validation, score, overrides and role."""

import copy
import tempfile
import unittest
from itertools import product
from pathlib import Path

import yaml

from optimizacion_portafolios.profiling import (
    DEFAULT_PATH,
    AnswerError,
    QuestionnaireError,
    category_for,
    emergency_fund_complete,
    evaluate,
    explain,
    level_for_score,
    load_questionnaire,
    parse_questionnaire,
    risk_score,
    suggest_role,
)

QUESTIONNAIRE = load_questionnaire()
RAW = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
HORIZONS = ("menos_1", "de_1_a_3", "de_3_a_5", "mas_5")
CAPITALS = ("menos_50k", "50k_200k", "200k_1m", "1m_5m", "5m_30m", "mas_30m")


def answers(**overrides):
    """Complete answers with a full emergency fund (nothing overrides the score)."""
    return {"k1": 3, "aporte": 3, "p2": 2, "p1": 3, "p6": 1, **overrides}


class QuestionnaireFileTests(unittest.TestCase):
    def test_file_content(self):
        q = QUESTIONNAIRE
        self.assertEqual(q.version, "2")
        self.assertEqual(q.level_ids, ("adverso", "moderado", "arriesgado"))
        self.assertEqual(q.level("adverso").label, "Conservador")
        self.assertEqual([block.layout for block in q.blocks], [
            ("k1", "capital_rango", "aporte"), ("horizonte", "p2", "p1"), ("p6",),
        ])
        self.assertEqual(q.question("k1").options, (
            "Solo la gasto", "Solo ahorro (cuenta, CDT, natillera)", "Invierto en fondos",
            "Invierto en acciones, ETF o cripto",
        ))
        self.assertEqual(q.question("aporte").options, (
            "Nada", "Menos de $50.000", "$50.000 a $200.000", "$200.000 a $500.000", "Más de $500.000",
        ))
        self.assertEqual(q.question("p1").options, ("Vendería todo", "Vendería una parte", "Esperaría", "Invertiría más"))
        self.assertEqual(q.question("p6").options, ("Sí", "Parcialmente", "No"))
        self.assertEqual(len(q.question_ids), 5)
        self.assertEqual([choice.id for choice in q.capital_options], list(CAPITALS))
        self.assertEqual([choice.id for choice in q.horizon_options], list(HORIZONS))
        self.assertEqual({(veto.question, veto.option_text) for veto in q.vetoes},
                         {("p6", "No"), ("p6", "Parcialmente")})
        self.assertEqual(q.max_score, 9)

    def test_capital_levels_live_in_the_yaml(self):
        levels = [QUESTIONNAIRE.capital_level(choice) for choice in CAPITALS]
        self.assertEqual(levels, ["bajo", "bajo", "bajo", "bajo", "medio", "alto"])

    def test_capital_and_horizon_of_version_1_profiles_are_still_readable(self):
        q = QUESTIONNAIRE
        self.assertEqual(q.capital_text_of("mas_100m"), "Más de 100 millones de pesos")
        self.assertEqual(q.horizon_text_of("mas_3"), "Más de 3 años")
        self.assertEqual(
            [q.capital_level(choice) for choice in ("menos_1m", "1m_5m", "5m_20m", "20m_100m", "mas_100m")],
            ["bajo", "bajo", "medio", "medio", "alto"],
        )
        with self.assertRaises(AnswerError):  # but they are not valid answers
            evaluate(q, "mas_100m", "mas_5", answers())

    def _broken(self, mutate):
        data = copy.deepcopy(RAW)
        mutate(data)
        with self.assertRaises(QuestionnaireError) as caught:
            parse_questionnaire(data, source="prueba.yaml")
        self.assertIn("prueba.yaml", str(caught.exception))
        return str(caught.exception)

    def test_invalid_files_fail_with_a_clear_message(self):
        self.assertIn("version", self._broken(lambda d: d.pop("version")))
        self.assertIn("al menos 2 opciones", self._broken(
            lambda d: d["bloques"][2]["preguntas"][0].update(opciones=["Sí"])))
        self.assertIn("no es una opción", self._broken(lambda d: d["vetos"][0].update(opcion="Casi")))
        self.assertIn("desempate", self._broken(lambda d: d["factor_limitante"]["desempate"].remove("horizonte")))
        self.assertIn("no es un nivel", self._broken(
            lambda d: d["datos_previos"]["horizonte"]["opciones"][0].update(nivel_maximo="extremo")))
        self.assertIn("umbrales", self._broken(lambda d: d["umbrales"].pop("arriesgado")))
        self.assertIn("crecer", self._broken(lambda d: d["umbrales"].update(moderado=8)))
        self.assertIn("puntaje máximo", self._broken(lambda d: d["umbrales"].update(arriesgado=10)))
        self.assertIn("rol sugerible", self._broken(lambda d: d["rol_sugerido"].update(si_cumple="gestor")))
        self.assertIn("marcador", self._broken(
            lambda d: d["factor_limitante"]["explicaciones"].update(tolerancia="{desconocido}")))
        self.assertIn("repetido", self._broken(lambda d: d["bloques"][1]["preguntas"][1].update(id="k1")))
        self.assertIn("nivel", self._broken(lambda d: d["datos_previos"]["capital"]["opciones"][0].pop("nivel")))
        self.assertIn("ubicar", self._broken(lambda d: d["bloques"][0]["preguntas"].pop(1)))
        self.assertIn("puntaje.preguntas", self._broken(lambda d: d["puntaje"]["preguntas"].append("p9")))

    def test_missing_or_malformed_yaml(self):
        with self.assertRaises(QuestionnaireError):
            load_questionnaire(Path(tempfile.gettempdir()) / "no-existe-cuestionario.yaml")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "malo.yaml"
            path.write_text("version: 1\nniveles: [\n", encoding="utf-8")
            with self.assertRaises(QuestionnaireError) as caught:
                load_questionnaire(path)
            self.assertIn("YAML no es válido", str(caught.exception))


class ScoreTests(unittest.TestCase):
    def test_the_score_adds_questions_4_5_and_6_from_0_to_3(self):
        for points in product(range(4), repeat=3):
            horizon, goal, drop = points
            given = answers(p2=goal + 1, p1=drop + 1)
            self.assertEqual(risk_score(QUESTIONNAIRE, HORIZONS[horizon], given), sum(points), points)

    def test_thresholds(self):
        cases = {0: "adverso", 3: "adverso", 4: "moderado", 6: "moderado", 7: "arriesgado", 9: "arriesgado"}
        for score, level in cases.items():
            self.assertEqual(level_for_score(QUESTIONNAIRE, score), level, score)

    def test_result_fields(self):
        result = evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", answers())
        self.assertEqual(result.scores, {"tolerancia": 6.0, "capacidad": 1.0})
        self.assertEqual(result.block_levels, {"tolerancia": "moderado", "capacidad": "arriesgado"})
        self.assertEqual((result.final_level, result.limiting_factor), ("moderado", "tolerancia"))
        self.assertEqual(result.horizon_level, "arriesgado")
        self.assertFalse(result.horizon_capped)

    def test_incomplete_or_invalid_answers(self):
        partial = answers()
        partial.pop("aporte")
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", partial)
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", answers(p2=5))
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", answers(p6=4))
        self.assertEqual(evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", answers(aporte=5)).answers["aporte"], 5)
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "otro", "mas_5", answers())
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_30m", "nunca", answers())


class FinalProfileTests(unittest.TestCase):
    RISKY = {"p2": 4, "p1": 4}

    def test_less_than_a_year_is_conservative(self):
        result = evaluate(QUESTIONNAIRE, "mas_30m", "menos_1", answers(**self.RISKY))
        self.assertEqual(result.scores["tolerancia"], 6.0)
        self.assertEqual((result.final_level, result.limiting_factor), ("adverso", "horizonte"))
        self.assertIn("menos de 1 año", explain(QUESTIONNAIRE, result))
        self.assertIn("Conservador", explain(QUESTIONNAIRE, result))

    def test_longer_horizons_do_not_cap_the_profile(self):
        for horizon in HORIZONS[1:]:
            self.assertFalse(evaluate(QUESTIONNAIRE, "mas_30m", horizon, answers(**self.RISKY)).horizon_capped)
        self.assertEqual(evaluate(QUESTIONNAIRE, "mas_30m", "de_1_a_3", answers(**self.RISKY)).final_level, "arriesgado")

    def test_without_a_full_emergency_fund_the_profile_is_conservative(self):
        for option in (2, 3):
            with self.subTest(option=option):
                result = evaluate(QUESTIONNAIRE, "mas_30m", "mas_5", answers(p6=option, **self.RISKY))
                self.assertEqual(result.scores["tolerancia"], 9.0)
                self.assertEqual((result.final_level, result.limiting_factor), ("adverso", "veto"))
                self.assertEqual(result.block_levels["capacidad"], "adverso")
                self.assertEqual(explain(QUESTIONNAIRE, result), result.veto.explanation)
        self.assertIsNone(evaluate(QUESTIONNAIRE, "mas_30m", "mas_5", answers(**self.RISKY)).veto)

    def test_selling_everything_is_no_longer_an_override(self):
        result = evaluate(QUESTIONNAIRE, "mas_30m", "mas_5", answers(p2=4, p1=1))
        self.assertEqual((result.final_level, result.limiting_factor), ("moderado", "tolerancia"))

    def test_a_veto_wins_the_tie_break_over_the_horizon(self):
        result = evaluate(QUESTIONNAIRE, "mas_30m", "menos_1", answers(p6=3))
        self.assertEqual(result.limiting_factor, "veto")

    def test_capital_contribution_and_experience_do_not_change_the_profile(self):
        base = evaluate(QUESTIONNAIRE, "5m_30m", "mas_5", answers())
        for capital, aporte, k1 in product(CAPITALS, range(1, 6), range(1, 5)):
            result = evaluate(QUESTIONNAIRE, capital, "mas_5", answers(aporte=aporte, k1=k1))
            self.assertEqual((result.final_level, result.limiting_factor, result.scores),
                             (base.final_level, base.limiting_factor, base.scores))

    def test_explanations_are_plain_language(self):
        result = evaluate(QUESTIONNAIRE, "1m_5m", "mas_5", answers())
        self.assertIn("6 de 9", explain(QUESTIONNAIRE, result))
        self.assertIn("«Moderado»", explain(QUESTIONNAIRE, result))


class CategoryTests(unittest.TestCase):
    def test_the_matrix(self):
        expected = {
            (1, "adverso"): "inicial", (1, "moderado"): "inicial", (1, "arriesgado"): "intermedio",
            (2, "adverso"): "inicial", (2, "moderado"): "inicial", (2, "arriesgado"): "intermedio",
            (3, "adverso"): "intermedio", (3, "moderado"): "avanzado", (3, "arriesgado"): "avanzado",
            (4, "adverso"): "intermedio", (4, "moderado"): "avanzado", (4, "arriesgado"): "avanzado",
        }
        for (k1, level), category in expected.items():
            self.assertEqual(category_for(QUESTIONNAIRE, level, {"k1": k1}).id, category, (k1, level))

    def test_a_version_1_profile_gets_a_category_too(self):
        self.assertEqual(category_for(QUESTIONNAIRE, "moderado", {"k1": 3, "k2": 2}).id, "avanzado")
        self.assertEqual(category_for(QUESTIONNAIRE, "moderado", {}).id, "inicial")

    def test_emergency_fund(self):
        version = QUESTIONNAIRE.version
        self.assertTrue(emergency_fund_complete(QUESTIONNAIRE, version, {"p6": 1}))
        self.assertFalse(emergency_fund_complete(QUESTIONNAIRE, version, {"p6": 2}))
        self.assertFalse(emergency_fund_complete(QUESTIONNAIRE, version, {"p6": 3}))
        # Version 1 asked for months: "Menos de 1", "1 a 3", "3 a 6", "Más de 6".
        self.assertFalse(emergency_fund_complete(QUESTIONNAIRE, "1", {"p6": 2}))
        self.assertTrue(emergency_fund_complete(QUESTIONNAIRE, "1", {"p6": 3}))

    def test_a_bad_matrix_fails_with_a_clear_message(self):
        data = copy.deepcopy(RAW)
        data["categorias"]["matriz"]["ya_invierte"]["moderado"] = "experto"
        with self.assertRaisesRegex(QuestionnaireError, "categorias.matriz.ya_invierte.moderado"):
            parse_questionnaire(data, source="prueba.yaml")


class SuggestedRoleTests(unittest.TestCase):
    def test_route_b_suggests_inversionista(self):
        cases = {1: "novato", 2: "novato", 3: "inversionista", 4: "inversionista"}
        for k1, role in cases.items():
            self.assertEqual(suggest_role(QUESTIONNAIRE, {"k1": k1}), role, k1)
            self.assertEqual(evaluate(QUESTIONNAIRE, "1m_5m", "mas_5", answers(k1=k1)).suggested_role, role)


if __name__ == "__main__":
    unittest.main()
