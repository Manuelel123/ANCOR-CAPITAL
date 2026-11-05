"""HTTP tests shared by every page (rendering, CSRF, shell and home page)."""

import re
import unittest

from support import WebTestCase


class WebInterfaceTests(WebTestCase):
    def test_all_primary_pages_render(self) -> None:
        for path, title in (
            ("/", "Construye portafolios con"),  # the gestor's original home
            ("/monitoring", "Monitoreo de activos"),
            ("/hrp", "Optimización HRP"),
            ("/black-litterman", "Black-Litterman"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title.encode(), response.data)
                self.assertIn(b'class="site-header"', response.data)

    def test_navigation_marks_current_page(self) -> None:
        for path in ("/", "/monitoring", "/hrp", "/black-litterman"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                current = re.findall(
                    r'class="site-nav__link" href="([^"]+)" aria-current="page"', html
                )
                self.assertEqual(current, [path])

    def test_post_requires_csrf_token(self) -> None:
        response = self.client.post("/hrp", data={"action": "optimize"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("sesión del formulario expiró".encode(), response.data)
        self.assertIn(b'class="error-page"', response.data)

    def test_unknown_page_renders_error_template(self) -> None:
        response = self.client.get("/no-existe")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Página no encontrada".encode(), response.data)

    def test_gestor_home_is_the_original_one(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        modules = re.findall(r'<a class="module-card" href="([^"]+)"', html)
        self.assertEqual(modules, ["/monitoring", "/hrp", "/black-litterman"])
        self.assertNotIn("Tu perfil vigente", html)

    def test_portada_shows_real_catalog_stats(self) -> None:
        from optimizacion_portafolios.web.main.routes import catalog_asset_count

        self.client.post("/auth/salir", data={"csrf_token": self.csrf_token})
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f"<dd>{catalog_asset_count()}</dd>", html)
        self.assertGreater(catalog_asset_count(), 30)
        self.assertIn("Hierarchical Risk Parity", html)

    def test_base_includes_theme_toggle_and_new_identity(self) -> None:
        html = self.client.get("/hrp").get_data(as_text=True)
        self.assertIn("data-theme-toggle", html)
        self.assertIn('localStorage.getItem("atlas-theme")', html)
        self.assertIn("atlas:themechange", html)
        self.assertIn('<meta name="theme-color" content="#0b1f4b">', html)
        self.assertIn("Inter+Tight", html)
        self.assertIn('classList.add("js")', html)
        self.assertIn("js/forms.js", html)
        for retired in ("Playfair", "#c19a5b", "#0b1f3a", "#13294b"):
            self.assertNotIn(retired, html)

    def test_stylesheet_drops_navy_and_gold(self) -> None:
        css = self.client.get("/static/css/app.css").get_data(as_text=True)
        self.assertIn('[data-theme="dark"]', css)
        self.assertIn("prefers-color-scheme: dark", css)
        for retired in ("--navy", "--gold", "Playfair", "#c19a5b"):
            self.assertNotIn(retired, css)


if __name__ == "__main__":
    unittest.main()
