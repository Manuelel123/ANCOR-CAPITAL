# UX de formularios, resultados y arquitectura de gráficos — Ancor Capital

> Decisiones del orquestador que PREVALECEN sobre este documento y sobre docs/diseno_visual.md:
> - Colores: SIEMPRE los tokens/paleta de docs/diseno_visual.md (índigo + paleta categórica validada). Ignora cualquier mención a navy, dorado, PRIMARY_COLOR/ACCENT_COLOR antiguos o #a23b3b. Gráfico de asignación: barras en serie slot 1 (#2a78d6 claro / #3987e5 oscuro); negativos en --negative-chart; marcador de peso de mercado/referencias en --text-3/--text-1 discontinuo.
> - Fondos de figuras Plotly transparentes; los colores dependientes del tema se aplican en el cliente leyendo variables CSS (soporte de modo oscuro). El template "atlas" en Python NO fija colores de fondo.
> - Formato numérico: punto decimal (`25.00%`, separators ".," en Plotly) por coherencia con tablas y tests. Ignora la sugerencia ",." del documento de diseño.
> - Análisis opcionales: acordeones `<details class="analysis-section">` (todas cerradas por defecto) con el estilo de acordeón de docs/diseno_visual.md; NO pestañas.
> - Mecanismo: opción A (un POST calcula todo; secciones ocultas que se dibujan al abrirse) + caché TTL de precios en data/market_data.py.
> - CDN: plotly.js 4.1.1 (versión de plotly.py 7.1.0; verificar URL — bundle cartesian si existe, si no el completo) y lightweight-charts 5.x fijo (verificar versión existente en unpkg). El tema claro/oscuro y layout general siguen docs/diseno_visual.md.

## 0. Hallazgos del código
1. Declarar `plotly>=7.1,<8` en pyproject (hoy solo transitiva vía skfolio). plotly.py 7.1.0 ↔ plotly.js 4.1.1 (`plotly/offline/_plotlyjs_version.py`).
2. Bug: BL tasa libre de riesgo `step="0.25"` y views `step="0.5"` bloquean valores válidos → `step="any"`.
3. Filas por activo (HRP `min_<T>`/`max_<T>`, BL `view_<T>`) solo se dibujan para `selected_tickers` del servidor; al marcar un activo nuevo no aparece su fila hasta "Actualizar lista" (origen del error "Define la view anual de: …"). Resolver con JS + `<template>`.
4. HRP: en `hrp/services.py::run_hrp` la tabla `weights_frame` repite el ticker en "Activo" y "Ticker" → construir mapa `names` como en BL.
5. BL "Máxima utilidad sin restricción long-only" admite pesos negativos/>100 % → descarta donut/treemap; usar barras horizontales con eje en 0.
6. Hoy el coste mayor tras la descarga es matplotlib (PNG base64 100–250 KB c/u; HTML 1–2 MB). Con Plotly el HTML baja a ~300–500 KB.
7. El refresco de universo es un POST `action=refresh`; los universos son dicts estáticos → embeber como JSON y resolver en cliente.
8. Cadenas/atributos fijados por tests (preservar o actualizar tests conscientemente): atributos en orden exacto `id="min_MSFT" name="min_MSFT" type="number"`, `name="min_weight" value="10"`, `name="view_NVDA" value="25.0"`, `id="view_NVDA"`, `value="95%"`; celda `<td>25.00%</td>`; textos «Distribución del portafolio», «Análisis histórico», «Descargar informe QuantStats», «Covarianza posterior», «Retorno prior (equilibrio)», «Ratio de Sortino esperado (modelo)», «Ratio de Sortino histórico», «Ratio de Sortino (QuantStats»; `<option value="AAPL" selected>`; «VH mensual»; la regex `_hrp_weights` exige filas `<tr><td>TICKER</td><td>…</td><td>NN.NN%</td>` (ticker 1ª col, peso 3ª, `<td>` sin atributos).
9. skfolio `HierarchicalClustering.plot_dendrogram(heatmap=...)` devuelve go.Figure (ojo: aplica optimal_leaf_ordering al dibujar; el modelo usa optimal_ordering=False → indicarlo en la ayuda).
10. BL: la "ventana fija" viaja como hidden `start_date` aceptado por el servidor → el servidor debe ignorarla y usar siempre `date_years_ago(today, 2)`.

## 1. Flujo de entrada (HRP, BL, Monitoreo)
Un único `<form method="post">` con el mismo CSRF y los mismos `name`, dividido en pasos `<fieldset class="step">` con `<legend>`. Pie sticky con resumen ("12 activos · SPY · 09/10/2025 → hoy") + botón Calcular.
Componentes (macros Jinja en `web/templates/macros/forms.html` + `static/js/forms.js`):
- Selector de activos con chips y búsqueda: render servidor de `<input type="checkbox" name="tickers">` como chips (funciona sin JS); buscador "Buscar ticker o nombre…", contador "12 de 19 seleccionados", botones Todos/Ninguno/Invertir. Para "Selección directa de activos" (>70) agrupar con subtítulos (Acciones, Índices sectoriales, Criptomonedas, Commodities, Portafolio imagen, Colombia) → añadir `GRUPOS_ACTIVOS` en catalogs.py.
- Universo: se mantiene `<select name="universe">` con descripción breve ("19 activos · ETFs sectoriales, acciones y cripto").
- Tickers personalizados como chips: Enter/coma/pegar crea chip; validación cliente con la regex de `forms.py::_TICKER_PATTERN` (`^[A-Z0-9^=.\-]{1,24}$` tras toUpperCase), chip inválido en rojo "Símbolo no válido"; valor real en `<input type="hidden" name="custom_tickers">` unido por comas. Ayuda: "Usa símbolos de Yahoo Finance: AMZN, BTC-USD, GC=F, ^GSPC, ECOPETROL.CL".
- Input % con sufijo: `<div class="input-affix"><input type="number" step="any" inputmode="decimal"><span>%</span></div>` (valores en puntos porcentuales como hoy).
- Ayuda: botón ⓘ con `aria-describedby` → `<span role="tooltip">`.
- Fecha con presets 1A·2A·3A·5A·Personalizada que escriben en `<input type="date" name="start_date">` (por defecto 1A).
- **Cambio de universo sin recarga** (mejora progresiva): la ruta pasa `universes_payload` = `{nombre: [{"ticker","label","group"}]}` embebido en `<script type="application/json" id="universes">{{ universes_payload|tojson }}</script>`. En `change` del select, JS reconstruye chips conservando marcados presentes en el nuevo universo; si no queda ninguno, selección por defecto (HRP todos; BL todos si "Portafolio actual" y los 4 primeros si no). El botón `name="action" value="refresh"` se queda con clase `js-hidden` (el JS añade `document.documentElement.classList.add('js')`); sin JS sigue funcionando (y el test de refresh).
- **Filas dependientes de la selección** (límites HRP, views BL): Jinja renderiza `<template id="limit-row">` / `<template id="view-row">` con la misma macro que las filas iniciales; JS clona/añade/quita filas al cambiar chips o tickers personalizados conservando valores escritos. `name`/`id` idénticos (`min_<T>`, `max_<T>`, `view_<T>`), orden de atributos `id, name, type`.
Validación cliente (setCustomValidity en español) + servidor (autoridad, ya existe): ≥2 activos (1 en Monitoreo) → botón deshabilitado con mensaje; formato ticker; rangos % (0–100, views −100–1000, rf 0–100) con step="any"; HRP mín ≤ máx, Σmín ≤ 100 % ≤ Σmáx en vivo replicando `WeightBounds.resolve`; fecha ≤ hoy.

### HRP — panel "Configura tu portafolio"
1. Universo y activos (visible): universo, chips, "+ Añadir tickers fuera del catálogo" (colapsable). Tooltip: «HRP agrupa los activos por similitud de correlación y reparte el riesgo de forma jerárquica. Mejor con 5 o más activos poco correlacionados.»
2. Periodo y referencia (visible): fecha con presets (ayuda «Más historia da correlaciones más estables, pero pesa más el pasado lejano.»); benchmark (ayuda «Solo se usa para comparar el desempeño histórico; no cambia los pesos HRP.»); en avanzadas "Usar otro benchmark" → `custom_benchmark`.
3. Restricciones de peso (`<details>` "Opciones avanzadas", abierto si hay límites por activo o globales ≠ 0/100): mínimo y máximo global (% con sufijo, 0 y 100); indicador de factibilidad en vivo ("Con 12 activos: mínimo global ≤ 8.33 %, máximo global ≥ 8.33 %", rojo si no); tabla "Límites por activo" Ticker | Mín. % | Máx. % (placeholder = global; se regenera con la selección). Ayuda «Vacío = usa el límite global. Los límites por activo tienen prioridad.»
Botón «Calcular portafolio HRP» (`name="action" value="optimize"`).

### BL — panel "Hipótesis del modelo"
1. Universo y activos (igual que HRP).
2. Objetivo y mercado: `<select name="objective">` con `<optgroup>` (mismos valores = claves de OBJECTIVES): «Rentabilidad ajustada por riesgo» (máx. Sharpe, máx. Sortino), «Minimizar riesgo» (mín. varianza, mín. CVaR 95%), «Rentabilidad / utilidad» (máx. rentabilidad, máx. utilidad δ), «Avanzado» (utilidad sin restricción long-only). Descripción dinámica `data-help` (Sharpe: «Maximiza el exceso de retorno por unidad de volatilidad.»; CVaR: «Minimiza la pérdida media del peor 5 % de los días.»; Utilidad: «Maximiza w'μ − δ/2·w'Σw con la aversión al riesgo implícita del mercado.»). Aviso de "sin restricción long-only" mostrado/ocultado en cliente al instante. Tasa libre de riesgo % (`step="any"`, 2.0; ayuda «Usa la tasa de un bono del Tesoro a corto plazo (T-Bill 3M), en la misma moneda que los activos.»). Benchmark (ayuda «Estima la aversión al riesgo δ = (E[rm] − rf)/Var(rm) y sirve de referencia histórica.»). Ventana: chip informativo «Ventana fija: 2 años (09/10/2024 – hoy)»; el servidor fija la fecha (hallazgo 10).
3. Views anuales (visible, corazón del modelo): tabla editable por activo generada con `<template>`: Ticker | Activo | View anual (%) | Equilibrio (%) [solo lectura si se conoce] | Confianza (`<input value="95%" disabled>` como badge, tooltip «Confianza fija del 95 % (método de Idzorek).»). Acciones: «Aplicar el mismo valor a todas», «Usar retornos de equilibrio», «Restablecer 8 %». Ayuda: «Tu view es el retorno total anual que esperas. Si coincide con el de equilibrio, el activo queda neutral y no inclina el portafolio.» Fase 1: tras un cálculo, columna Equilibrio con `prior_returns` del resultado; "Usar equilibrio" copia esos valores. (Fase 2 opcional: endpoint `POST /black-litterman/equilibrium`.) Sin datos de equilibrio: 8.0.

### Monitoreo
Se conservan pestañas (`view`). Selector de portafolio en cliente (mismo patrón; botón refresh como respaldo). Chips con búsqueda (38 opciones). Presets de fecha. `volatility_ticker` pasa a selector DENTRO del resultado que cambia la traza en cliente (mantener `<option value="AAPL" selected>` en ese selector por el test). `period` sigue siendo parámetro de servidor. Fundamental: select de compañía con filtro JS.

## 2. Resultados
**Inmediato**: barra de contexto una línea («RESULTADO HRP · 09/10/2025 — 09/10/2026 · 19 activos · Benchmark SPY») + avisos de faltantes; luego tarjeta «Distribución del portafolio» con:
- **Gráfico de asignación**: barras horizontales ordenadas Plotly (no donut/treemap: >20 porciones ilegibles y no admiten negativos). Orden descendente arriba→abajo (`yaxis.autorange="reversed"`), etiqueta de valor al final (`texttemplate="%{x:.2%}"`), `tickformat=".0%"`, hover «Activo · Ticker · Peso», altura ≈ 28px·n + 80. HRP: línea vertical punteada en 1/N «Peso equitativo» y, si hay límites, marcadores tenues del rango mín–máx. BL: traza adicional con marcador de línea vertical (`marker.symbol="line-ns-open"`) para el peso de mercado (`model.market_weights`); negativos en color negativo.
- **Tabla exacta** (al lado, debajo en estrecho), construida con Jinja desde lista de dicts (`allocation_rows`) con `<tfoot>` total; botón «Copiar» / «Descargar CSV» (JS). HRP: Ticker | Activo | Peso óptimo (+ "Límite aplicado (mín–máx)" solo si hay límites), orden por peso desc, formato `25.00%`, pie «Total 100.00%», primeras tres celdas `<td>` sin atributos (regex de tests). BL: Ticker | Activo | Peso BL | Peso de mercado | Inclinación (pp) | View anual | Retorno posterior, pie total; «View anual» conserva `<td>25.00%</td>`; capitalización, confianza y prior pasan a «Supuestos del modelo».
- Un resumen KPI compacto encima es aceptable (p. ej. HRP: Sortino, nº activos; BL: retorno esperado, volatilidad, Sharpe) siguiendo el componente KPI del diseño, pero lo central e inmediato es el gráfico + tabla.

**Secciones opcionales** (`<details class="analysis-section" data-section="...">`, cerradas, summary con título + línea de contenido, "Expandir todo / Contraer todo"):
- HRP: (1) Riesgo y desempeño (Sortino skfolio, peso mín/máx asignado, observaciones, suma de pesos + tabla métricas QuantStats y nota de Sortino). (2) «Análisis histórico · {benchmark}» (rendimiento acumulado, drawdown, rendimientos mensuales heatmap, Sharpe móvil 126d, distribución de retornos mensuales, retornos anuales, botón «Descargar informe QuantStats»). (3) Correlación y clusters (heatmap cuasi-diagonal, dendrograma skfolio, tabla de correlación en details anidado «Ver valores»). (4) Contribución por activo (tabla calculate_hrp_contributions, barras de contribución al riesgo RC_i = w_i(Σw)_i / w'Σw, participación del retorno). (5) Volatilidad de los activos (histograma de volatilidad mensual con selector de activo + línea Actual; box plot de retornos diarios por activo). (6) Evolución de precios (Lightweight Charts, selector de activo, interruptor Precio / Base 100). (7) Datos y supuestos (periodo efectivo, observaciones, common_observations, tickers sin datos, límites aplicados, método).
- BL: (1) Métricas esperadas del modelo (retorno, volatilidad, Sharpe y «Ratio de Sortino esperado (modelo)», δ). (2) Del equilibrio a la posterior (barras agrupadas/dumbbell Prior → View → Posterior + tabla con «Retorno prior (equilibrio)», view, confianza, posterior). (3) «Covarianza posterior» (heatmap de correlación derivada + tabla exacta anualizada 6 decimales en «Ver valores»). (4) Análisis histórico vs benchmark (métricas QuantStats con «Ratio de Sortino histórico» + mismos gráficos Plotly que HRP + descarga del informe). (5) Evolución de precios (Lightweight Charts). (6) Supuestos del modelo (δ, τ, confianza 95 %, capitalización/activos netos por activo, ventana 2 años, objetivo, rf).
- Monitoreo (excepción): precios = contenido principal → Lightweight Charts inmediato; retornos, retornos esperados, volatilidad y estadísticas en acordeón; tablas recientes en details.

**Mecanismo**: opción A (todo en el POST; secciones ocultas renderizadas al abrirse) + caché TTL 15 min de `download_prices` en data/market_data.py por clave (tickers, start, end) devolviendo `.copy()` (acelera descarga de informe y recálculos; los tests parchean services.download_prices, no interfiere).

## 3. Inventario de gráficos
| # | Gráfico | Librería/tipo | Datos | Dónde |
|---|---|---|---|---|
| 1 | Pesos HRP | Plotly barras H + 1/N (+ rango límites) | weights, bounds | HRP inmediato |
| 2 | Pesos BL | Plotly barras H + marcador peso de mercado; negativos en rojo | model.weights, model.market_weights | BL inmediato |
| 3 | Tabla de asignación | HTML con total y CSV | pesos, nombres, límites / mercado, views, posterior | inmediato |
| 4 | Rendimiento acumulado | Plotly líneas portafolio vs benchmark (qs.stats.compsum) | aligned | Análisis histórico |
| 5 | Drawdown | Plotly área tozeroy (qs.stats.to_drawdown_series), marcar peor caída | retornos portafolio | Análisis histórico |
| 6 | Rendimientos mensuales | Plotly heatmap año×mes (+ col. Año), qs.stats.monthly_returns, zmid 0, texttemplate %{z:.1%}, escala divergente | retornos portafolio | Análisis histórico |
| 7 | Sharpe móvil | Plotly líneas qs.stats.rolling_sharpe(r, rolling_period=126) portafolio y benchmark + media; None si <126 obs | aligned | Análisis histórico |
| 8 | Distribución de retornos mensuales | Plotly histograma superpuesto | aligned mensual | Análisis histórico |
| 9 | Retornos anuales | Plotly barras agrupadas | aligned por año | Análisis histórico |
| 10 | Correlación | Plotly heatmap (anotaciones si n≤18), zmin −1 zmax 1 | ordered_correlation | HRP correlación |
| 11 | Dendrograma | Plotly (skfolio plot_dendrogram(heatmap=False) re-tematizado) | clustering ajustado (exponer en HrpResult) | HRP correlación |
| 12 | Contribución riesgo/retorno | Plotly barras H | returns.cov(), weights; calculate_hrp_contributions | HRP contribución |
| 13 | Volatilidad mensual | Plotly histograma + línea Actual, selector de activo (una traza visible) | calculate_monthly_volatility | HRP volatilidad; Monitoreo portafolio |
| 14 | Retornos por activo | Plotly box plot por activo | returns | HRP volatilidad; Monitoreo retornos |
| 15 | Retorno diario del portafolio | se elimina (cubierto por 4 y 8) | — | — |
| 16 | Evolución de precios / unitaria | Lightweight Charts área/línea por activo con selector, Precio/Base 100, benchmark en base 100, rangos 1M/3M/6M/YTD/1A/Todo | precios crudos por ticker + benchmark | HRP/BL opcional; Monitoreo inmediato |
| 17 | Prior → view → posterior | Plotly barras agrupadas/dumbbell | prior_returns, views, posterior_returns | BL |
| 18 | Correlación posterior | Plotly heatmap + tabla exacta covarianza | posterior_covariance | BL |
| 19 | VH por periodo (opciones, 38) | Plotly barras H ordenadas de VH anualizada actual + histograma del activo elegido (selector) | calculate_historical_volatility | Monitoreo opciones |
| 20 | Volatilidad (monitoreo portafolio) | Plotly histograma (= 13) | monthly vol + current_vol | Monitoreo portafolio |
| 21 | Métricas QS, contribuciones, estadísticas, retornos esperados | Tablas (metrics_html / dataframe_html) | — | opcionales |
El informe QuantStats descargable sigue usando matplotlib → mantener `matplotlib.use("Agg")` en web/__init__.py.

## 4. Arquitectura técnica
**`web/common/charts.py`** (sin matplotlib): template Plotly "atlas" (pequeño; NO usar el template por defecto "plotly", añade ~8–10 KB/figura). Helper:
```python
def figure_payload(fig: go.Figure) -> Markup:
    text = fig.to_json(validate=False, remove_uids=True)  # NaN→null, numpy→bdata
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
```
Funciones públicas → `Markup | None` (None = sin datos): `allocation_chart(weights, names, *, reference=None, market_weights=None, bounds=None)`, `cumulative_returns_chart(portfolio, benchmark)`, `drawdown_chart(portfolio)`, `monthly_returns_heatmap(portfolio)`, `rolling_sharpe_chart(portfolio, benchmark, period=126)` (None si len<period), `returns_distribution_chart(portfolio, benchmark)`, `yearly_returns_chart(portfolio, benchmark)`, `correlation_heatmap(matrix, title)`, `dendrogram_chart(clustering)`, `contribution_chart(frame, column)`, `volatility_histogram_chart(monthly_vol, current)` (multi-activo con selector), `returns_box_chart(returns)`, `prior_posterior_chart(prior, views, posterior)`, `period_volatility_chart(annualized, period_vol, label)`. `historical_charts(portfolio, benchmark, rolling_period) -> tuple[dict[str, Markup], bool]` sustituye a `quantstats_charts` (misma forma de retorno).
Claves ASCII estables de `result["charts"]`: allocation, cumulative, drawdown, monthly_heatmap, rolling_sharpe, returns_distribution, yearly_returns, correlation, dendrogram, risk_contribution, monthly_volatility, returns_box, prior_posterior, posterior_correlation. Títulos en la plantilla.
**`web/common/price_series.py`**:
```python
def price_series_payload(raw_prices: pd.DataFrame, names: dict[str, str], benchmark: pd.Series | None = None) -> Markup:
    # {"series": [{"ticker": "AAPL", "name": "Apple", "time": ["2025-10-09", ...], "value": [227.48, ...]}], "benchmark": {...} | null}
```
dropna POR ticker usando precios ANTES de ffill().dropna() (añadir `raw_prices` a HrpRun y equivalente BL); fechas YYYY-MM-DD; round(v, 6); sin NaN; formato columnar; escapado como figure_payload. View models: `result["charts"]` dict de Markup + `result["prices_payload"]`; BL `history["charts"]` con claves nuevas y `weights_chart` → `charts["allocation"]`; tabla de asignación `result["allocation_rows"]` (lista de dicts) renderizada por macro con `<tfoot>`.
**Macros `web/templates/macros/charts.html`**:
```jinja
{% macro plotly_chart(key, payload, title, height=360) %}
<figure class="chart-card"><figcaption>{{ title }}</figcaption>
  <div class="plotly-chart" data-plotly="chart-{{ key }}" style="min-height:{{ height }}px" role="img" aria-label="{{ title }}"></div>
  <script type="application/json" id="chart-{{ key }}">{{ payload }}</script>
  <noscript><p class="help-text">Activa JavaScript para ver el gráfico; los valores exactos están en la tabla.</p></noscript>
</figure>
{% endmacro %}
{% macro price_explorer(key, payload, title="Evolución de precios") %} contenedor [data-price-explorer] + select de activo + botones de rango + <script type="application/json" id="prices-{{ key }}"> {% endmacro %}
```
**Carga CDN** en `{% block page_scripts %}` de base.html (solo HRP, BL, Monitoreo), con defer: plotly.js 4.1.1 (cartesian si existe; verificar URL; SRI opcional), lightweight-charts 5.x fijo (API v5 `chart.addSeries(LightweightCharts.AreaSeries, opts)`; mantener attributionLogo true por licencia Apache-2.0), `static/js/charts.js`, `static/js/forms.js`.
**`static/js/charts.js`**: render diferido — nunca `Plotly.newPlot` sobre contenedor oculto; renderVisible(document) al cargar; en `toggle` de `details.analysis-section` abierto → renderVisible(sección); si ya renderizado → `Plotly.Plots.resize`. Config {responsive:true, displaylogo:false, modeBarButtonsToRemove:["lasso2d","select2d"]}. Si `!window.Plotly` mostrar mensaje. `initPriceExplorer(el)` idempotente: createChart autoSize, AreaSeries; select de activo → series.setData + fitContent; botones de rango → setVisibleRange; interruptor Base 100 + benchmark LineSeries; leyenda con subscribeCrosshairMove; franja de estadísticas (último precio, variación del periodo, máx, mín); priceFormat precisión 4 si precio <1. Colores leídos de variables CSS (tema claro/oscuro) y re-aplicados al cambiar de tema.

## 4.4 Pruebas
- `tests/test_charts.py`: por función, json.loads(str(payload)) y comprobar tipo/número de trazas, orientation "h", zmid 0 en heatmap, None en Sharpe móvil con <126 obs, sin "<" ni NaN; price_series_payload con fechas ISO, longitudes iguales, sin NaN.
- test_web: mantener aserciones del hallazgo 8; nuevas: resultado contiene `cdn.plot.ly/plotly-`, `data-plotly="chart-allocation"`, `data-price-explorer`; ya no `data:image/png`; extraer `<script type="application/json" id="chart-allocation">` y validar nº de barras = nº activos; GET contiene `id="universes"` con "Portafolio actual".
- test_market_data: caché TTL devuelve copias y la 2ª llamada no vuelve a pedir datos.
