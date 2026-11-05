# Sistema de diseño · Ancor Capital — "Instrumento de precisión"

Interfaz clara y neutra con la identidad de Ancor Capital (azul marino #0b1f4b y un acento azul #1f6fd6, derivado del azul del logo #2f7bd9 para que el texto blanco cumpla contraste); el color con significado se reserva para los datos (series, verde/rojo ganancia/pérdida, escala de correlación). Modo claro por defecto + modo oscuro opcional (interruptor en la navbar, persistido en localStorage; también respeta prefers-color-scheme).
Referencias: Stripe Dashboard (jerarquía sobria, tarjetas blancas borde 1px, KPIs grandes), Linear (índigo, foco cuidado, transiciones 150ms), Koyfin/TradingView (tablas densas tabulares, crosshair, grid casi invisible), Vercel/Geist (bordes > sombras), Robinhood (verde/rojo solo para dirección).

Hoy la identidad navy+dorado está hardcodeada en base.html (theme-color #0b1f3a, favicon y logo SVG con #13294b/#c19a5b, carga Playfair Display), index.html (SVG hero con #c19a5b/#8e9bb0) y charts.py (SERIES_COLORS, CORRELATION_CMAP). Hay que retirarla de todos esos sitios.
Clases en uso a conservar (re-estilizar): .panel .content-grid .result-column .result-panel .result-grid .metric-card .chart-grid .chart-card .data-table-wrap .tabs .notice .flash-* .empty-state .check-grid .form-field .form-row details/summary .button-primary/secondary/ghost (button-gold desaparece).

## Tokens (claro)
```css
:root{
  color-scheme: light;
  --bg:#f7f8fa; --surface-1:#ffffff; --surface-2:#f1f3f6; --surface-3:#e8ebf0; --surface-inverse:#0b1f4b;
  --border:#e3e7ed; --border-strong:#cfd5de; --border-input:#8b95a7;
  --text-1:#0b1f4b; --text-2:#475569; --text-3:#5f6b7e; --text-disabled:#9aa3b2; --text-on-accent:#ffffff;
  --accent:#1f6fd6; --accent-hover:#1a5cb4; --accent-active:#0b1f4b; --accent-subtle:#eaf2fd; --accent-border:#bcd6f7;
  --focus-ring:0 0 0 3px rgba(47,123,217,.35);
  --success:#047857; --success-bg:#ecfdf5; --success-border:#a7f3d0;
  --warning:#b45309; --warning-bg:#fffbeb; --warning-border:#fde68a;
  --danger:#b91c1c;  --danger-bg:#fef2f2;  --danger-border:#fecaca;
  --info:#0369a1;    --info-bg:#f0f9ff;    --info-border:#bae6fd;
  --positive:#047857; --negative:#c62828; --positive-chart:#16a34a; --negative-chart:#dc2626;
}
```
## Tokens (oscuro) — `[data-theme="dark"]` y `@media (prefers-color-scheme: dark)` sobre `:root:where(:not([data-theme="light"]))`
```css
[data-theme="dark"]{
  color-scheme: dark;
  --bg:#081226; --surface-1:#0f1c36; --surface-2:#15243f; --surface-3:#1d2e4d; --surface-inverse:#e6e9ef;
  --border:#22324f; --border-strong:#2f4163; --border-input:#5c6578;
  --text-1:#e6e9ef; --text-2:#a3abb9; --text-3:#8a93a3; --text-disabled:#5c6578; --text-on-accent:#081226;
  --accent:#7ab0f5; --accent-hover:#9cc4f8; --accent-active:#bdd8fb; --accent-subtle:rgba(122,176,245,.14); --accent-border:rgba(129,140,248,.4);
  --focus-ring:0 0 0 3px rgba(122,176,245,.45);
  --success:#34d399; --success-bg:rgba(52,211,153,.12); --warning:#fbbf24; --warning-bg:rgba(251,191,36,.12);
  --danger:#f87171; --danger-bg:rgba(248,113,113,.12); --info:#38bdf8; --info-bg:rgba(56,189,248,.12);
  --positive:#34d399; --negative:#f87171; --positive-chart:#22c55e; --negative-chart:#ef4444;
}
```
## Paleta categórica de series (orden fijo, validada; no reordenar)
| # | Claro | Oscuro |
|---|---|---|
| 1 azul | #2a78d6 | #3987e5 |
| 2 naranja | #eb6834 | #d95926 |
| 3 aqua | #1baf7a | #199e70 |
| 4 amarillo | #eda100 | #c98500 |
| 5 magenta | #e87ba4 | #d55181 |
| 6 verde | #008300 | #008300 |
| 7 violeta | #4a3aa7 | #9085e9 |
| 8 rojo | #e34948 | #e66767 |
Reglas: >8 activos → no generar colores nuevos (agrupar "Otros", múltiplos pequeños, o líneas en gris --text-disabled resaltando la serie bajo el cursor). El color sigue al TICKER (asignar por orden de selected_tickers; mismo ticker = mismo color en toda la página). Aqua/amarillo/magenta <3:1 sobre blanco → siempre leyenda o etiqueta directa. Gráficos de una sola serie (pesos, histogramas) usan slot 1 #2a78d6; marcas especiales (volatilidad actual, etc.) en --text-1 discontinuo, nunca dorado.
Divergente correlación (−1..+1), azul ↔ gris neutro ↔ rojo: claro [-1 #104281, -0.6 #256abf, -0.3 #86b6ef, 0 #f0efec, 0.3 #f3a29f, 0.6 #d03b3b, 1 #8f1d1d]; oscuro mismos extremos con medio #383835. Texto de celda blanco si |ρ|>0.6, si no --text-1. (Rojo = correlación alta = menos diversificación.)
Secuencial (pesos/magnitudes): #cde2fb → #86b6ef → #3987e5 → #1c5cab → #0d366b.

## Tipografía (una petición a Google Fonts; eliminar Playfair)
Inter 400/500/600/700, Inter Tight 600/700, JetBrains Mono 400/500.
```css
--font-ui:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
--font-display:"Inter Tight",var(--font-ui);   /* h1/h2 y cifras KPI */
--font-mono:"JetBrains Mono",ui-monospace,monospace; /* tickers, chips, valores técnicos (τ) */
font-variant-numeric: tabular-nums lining-nums; /* tablas, KPIs, inputs numéricos */
--fs-xs:.75rem; --fs-sm:.8125rem; --fs-base:.9375rem; --fs-md:1rem; --fs-lg:1.125rem; --fs-xl:1.375rem; --fs-2xl:1.75rem; --fs-3xl:2.25rem; --fs-hero:clamp(2.25rem,4vw + 1rem,3.5rem);
--lh-tight:1.15; --lh-snug:1.35; --lh-body:1.6; --tracking-display:-0.02em; --tracking-eyebrow:.06em;
```
Inter con font-feature-settings "cv11","ss01". Cifras en Inter tabular (no mono).
Espaciado base 4: --sp-1:4px … --sp-2:8 --sp-3:12 --sp-4:16 --sp-5:20 --sp-6:24 --sp-8:32 --sp-10:40 --sp-12:48 --sp-16:64. Tarjetas 20px móvil / 24px escritorio; entre secciones 32–48px.
Radios: --r-xs:4 --r-sm:6 (inputs, botones) --r-md:10 (tarjetas) --r-lg:14 (paneles, hero) --r-full:999.
Sombras: --shadow-xs:0 1px 2px rgba(15,23,42,.05); --shadow-sm:0 1px 3px rgba(15,23,42,.08),0 1px 2px rgba(15,23,42,.04); --shadow-lg:0 12px 32px -8px rgba(15,23,42,.18). En oscuro ~0; elevación por superficies/bordes.
Transiciones: --ease:cubic-bezier(.2,.8,.2,1); --t-fast:120ms; --t-base:180ms; --t-slow:280ms; respetar prefers-reduced-motion.
Layout: --container:1280px; --sidebar:340px; --nav-h:60px.

## Componentes
- **Navbar**: sticky 60px, fondo color-mix(in srgb,var(--surface-1) 85%,transparent) + backdrop-filter blur(12px), borde inferior. Logo: monograma "A" en cuadrado 28px radio 8 fondo --accent trazo blanco + "Ancor Capital" (Inter Tight 600) + "Portfolio Lab" (--text-3); mismo SVG para favicon; theme-color #4f46e5. Enlaces 14px/500 --text-2, padding 8×12 radio 6; hover --surface-3/--text-1; activo --text-1 + barra inferior 2px --accent + aria-current="page". Derecha: píldora "● Tiingo · Yahoo" (punto 6px --success, 12px) + botón de tema (icono 36px). Móvil <768: menú <details> o pestañas con scroll horizontal.
- **Encabezado de página**: eyebrow 12px/600/mayúsculas --accent ("Optimización · Riesgo jerárquico"), h1 28px Inter Tight 700 tracking −0.02em, descripción --text-2 máx 65ch; acciones a la derecha opcionales; sin tarjeta; margen inferior 24px.
- **Tarjetas**: --surface-1, borde 1px --border, radio 10–14, --shadow-xs, padding 24. Cabecera: título 18px/600 + subtítulo 13px --text-3 + acciones a la derecha (botón fantasma). chart-card dentro de result-panel: planas (separador superior), sin tarjeta-en-tarjeta. Clicables (módulos inicio): hover borde --border-strong, --shadow-sm, translateY(-1px), flecha +2px; focus-visible anillo.
- **KPI .metric-card**: grid repeat(auto-fit,minmax(180px,1fr)) gap 12. Etiqueta 12px/500 --text-3 (+ "?" tooltip); cifra 28px (36 destacada) Inter Tight 600 tabular --text-1 lh 1.15, unidad al 60% en --text-3; línea de contexto 12px --text-3; delta opcional píldora (▲ verde / ▼ rojo, flecha siempre). Fila principal + fila de mini-KPIs (cifra 18px) para lo secundario.
- **Botones** alto 40 (36 pequeño), padding 0 16, radio 6, 14px/600, gap icono 8. Primario: --accent / hover --accent-hover / active --accent-active + scale(.98) / focus-visible outline 2px --accent offset 2 / disabled --surface-3 + --text-disabled / is-loading: "Calculando…" + spinner 16px, aria-busy, sin salto de ancho; ancho completo en el panel. Secundario: --surface-1, borde --border-strong, hover --surface-2. Fantasma: sin borde, --text-2, hover --surface-3. Peligro: texto --danger, hover --danger-bg.
- **Campos**: alto 40, fuente 16px, borde 1px --border-input, radio 6, padding 0 12; hover borde --text-3; focus borde --accent + --focus-ring; error borde --danger + anillo rgba(185,28,28,.25) + mensaje 12px --danger con icono, aria-invalid/aria-describedby; disabled --surface-2. Etiqueta arriba 13px/500 --text-1 (gap 6); ayuda 12px --text-3. Select appearance:none + chevron SVG. Date con accent-color.
  - **.input-affix** (número con sufijo %): flex con borde en el envoltorio que se ilumina con :focus-within; input alineado a la derecha tabular; sufijo "%" en --surface-2 con borde izquierdo; ocultar spinners; inputmode="decimal". Para pesos mín/máx, views BL, tasa libre de riesgo.
  - **Selector de activos (sustituye .check-grid)**: chips = <label class="chip"><input type="checkbox" class="visually-hidden">TICKER</label>; 32px, radio full, --font-mono 13px, borde --border-strong; marcado (:has(input:checked)) fondo --accent-subtle, borde --accent-border, texto --accent + "✓"; :has(:focus-visible) anillo. Encima: input de búsqueda con lupa que filtra chips (~10 líneas JS) + contador "6 de 30 seleccionados" + enlaces "Todos / Ninguno". Chip final "+ Añadir ticker" que enfoca el campo de tickers personalizados. >24 elementos: max-height 220px con scroll y degradado inferior.
- **Fieldsets**: <fieldset> sin borde, <legend> 12px/600/mayúsculas --text-3 tracking .06em, separador superior --border, padding 20. Grupos: "Universo y activos", "Periodo y benchmark", "Restricciones de peso", "Views". Sustituyen <hr> y h3.panel-title. .form-row 2 columnas desde 360px.
- **Pestañas**: segmentadas (contenedor --surface-2 radio 8 padding 4; pestaña 32px 13px/500 --text-2; activa --surface-1 + --shadow-xs + --text-1). Para vistas de monitoreo usar aria-current. Pestañas de análisis con subrayado 2px --accent. Móvil: scroll horizontal con scroll-snap.
- **Acordeón details/summary**: summary 44px 14px/500 --text-1 con chevron 16px que rota 180° en [open] (180ms); hover --surface-2; focus-visible anillo; quitar marcador nativo; contenido padding-top 16. Badge contador en summary (--accent-subtle).
- **Tablas financieras** (.data-table generadas por pandas to_html): width 100%, 13px, tabular-nums, border-collapse separate. Cabecera --surface-2 12px/600 --text-2, sticky top dentro de .data-table-wrap (max-height ~420px scroll), borde inferior --border-strong. Celdas 10×12, borde inferior --border. Números a la derecha (cabecera incluida), texto/tickers a la izquierda; primera columna --font-mono 500 sticky left. Sin zebra (hover fila --surface-2); zebra solo >10 columnas. Negativos en --negative con "−" real (U+2212). Pesos con barra de proporción opcional (linear-gradient --accent-subtle). .data-table-wrap borde 1px --border radio 10 overflow auto. Matrices de correlación/covarianza en tabla con celdas coloreadas por la escala divergente.
- **Avisos/flash**: fondo --{tipo}-bg, borde 1px --{tipo}-border + borde izquierdo 3px --{tipo}, radio 8, padding 12×16, icono 16px (✓ ⚠ ⓘ ✕), texto 14px --text-1. role="alert" errores, role="status" resto. Arriba del contenido, no toasts.
- **Estado vacío**: borde 1px dashed --border-strong radio 14, min-height 360 centrado; icono 48px en círculo --accent-subtle; título 18px/600; texto --text-2 máx 44ch; lista de 3 pasos ("1 Elige activos · 2 Ajusta límites · 3 Calcula"); opcional esqueleto atenuado.
- **Carga (4–15 s, POST recarga)**: botón "Calculando…" con spinner + overlay sobre .result-column (velo color-mix --bg 70% + blur 2px) con tarjeta centrada: spinner anillo 28px, título "Optimizando portafolio HRP", pasos que avanzan cada ~2,5 s con JS ("Descargando precios → Estimando covarianzas → Clustering jerárquico → Calculando métricas") con check, barra indeterminada 3px; aria-live="polite". Restaurar en pageshow.
- **Tooltips "?"**: botón 16px (círculo borde --border-strong, 11px --text-3), <button type="button" aria-describedby>; burbuja --surface-inverse con texto --bg, 12px lh 1.45, máx 260px, radio 6, padding 8×10, --shadow-lg, flecha 6px; :hover/:focus-visible; Esc cierra. Para Sortino, τ, Idzorek, prior de equilibrio, cuasi-diagonal (reemplaza párrafos largos de help-text).

## Tema de gráficos
**Plotly**: plantilla "atlas" en `web/common/charts.py` (+ módulo de tokens Python que replique los hex). Servir figuras con fig.to_json(); en cliente Plotly.newPlot. Un charts.js lee variables CSS con getComputedStyle y aplica Plotly.relayout al cambiar de tema → colores dependientes del tema no se fijan en Python; fondos transparentes.
```
layout: font {family "Inter, system-ui, sans-serif", size 12, color text-2}; sin title (el título va en el HTML de la tarjeta)
  paper_bgcolor/plot_bgcolor "rgba(0,0,0,0)"; colorway = 8 slots en orden; margin {l:56,r:16,t:24,b:44}; autosize
  ejes: showgrid (solo y en series temporales; solo x en barras horizontales), gridcolor border, zeroline true (border-strong), showline false, ticks "", tickfont 11 text-3, title.font 12 text-2, automargin; tickformat ".1%" retornos/pesos, ".2f" ratios
  hovermode "x unified" líneas / "closest" barras-heatmap; hoverlabel {bgcolor surface-1, bordercolor border-strong, font Inter 12 text-1, align left}
  xaxis spikes across, 1px, dot, text-3
  legend horizontal arriba-izquierda (x0, y1.02, yanchor bottom), 12px text-2, fondo transparente, itemclick toggle
  bargap .25, barcornerradius 4; separators ",." (formato español)
config: displaylogo false, responsive true, locale "es" (plotly-locale-es), modeBarButtonsToRemove [lasso2d, select2d, autoScale2d, toggleSpikelines], toImageButtonOptions {format png, scale 2}
trazas: líneas width 2 (1.5 si >4 series); pesos → barras horizontales ordenadas slot 1 con texto exterior "12,4 %" y hovertemplate "%{y}: %{x:.2%}<extra></extra>"; histograma slot 1 con marker.line superficie 1px y línea "actual" vertical discontinua text-1 + anotación; drawdown área tozeroy negative-chart 1.5px fill rgba(220,38,38,.12); retornos diarios barras por signo; acumulado: portafolio slot1 2px, benchmark text-3 1.5px dot; heatmap correlación con la escala divergente zmin −1 zmid 0 zmax 1, xgap/ygap 2, texttemplate "%{z:.2f}" si n≤15, colorbar thickness 10 sin borde.
alturas por CSS: .plot{height:360px} (280 móvil, 520 máx heatmaps). Múltiplos de histogramas: retícula CSS de mini-gráficos de 220px o make_subplots.
```
**Lightweight Charts v5** (unpkg lightweight-charts@5 standalone):
```
createChart(el,{autoSize:true, layout:{background:{type:'solid',color:'transparent'}, textColor:css('--text-3'), fontFamily:'Inter, system-ui, sans-serif', fontSize:12, attributionLogo:true},
 grid:{vertLines:{visible:false}, horzLines:{color:css('--border')}},
 crosshair:{mode:CrosshairMode.Magnet, vertLine/horzLine:{color:css('--text-3'), width:1, style:LineStyle.Dashed, labelBackgroundColor:css('--surface-inverse')}},
 rightPriceScale:{borderVisible:false, scaleMargins:{top:.12,bottom:.08}}, timeScale:{borderVisible:false, rightOffset:4, fixLeftEdge:true, fixRightEdge:true},
 localization:{locale:'es-CO', priceFormatter:p=>p.toLocaleString('es-CO',{minimumFractionDigits:2,maximumFractionDigits:2})}})
Un activo: AreaSeries lineColor serie-1 lineWidth 2, topColor rgba(42,120,214,.22) bottomColor rgba(42,120,214,0), priceLine punteada text-3, lastValueVisible, crosshairMarkerRadius 4.
Varios activos: LineSeries por ticker con slots categóricos, preferible en base 100 (un solo eje, nunca doble eje).
```
Encima del gráfico: botones segmentados de rango (1M, 3M, 6M, YTD, 1A, 5A, Máx) → timeScale().setVisibleRange. Leyenda flotante a la izquierda: ticker mono, último precio 18px tabular, variación del periodo en píldora verde/roja ▲/▼, actualizada con subscribeCrosshairMove. Cambio de tema → chart.applyOptions.

## Layout
- Contenedor máx 1280px, márgenes 16/24/32 (móvil/tableta/escritorio), fondo --bg; opcional radial muy tenue en el hero: radial-gradient(1200px 400px at 70% -10%, rgba(79,70,229,.08), transparent).
- **Inicio**: (1) hero 2 columnas 7/5 ≥1024px: eyebrow "Laboratorio de portafolios cuantitativos", h1 --fs-hero Inter Tight 700 tracking −0.03em ("Construye portafolios con método, no con intuición.", una palabra en --accent), subtítulo 18px --text-2 máx 52ch, CTA primario "Explorar mercado →" + secundario "Optimizar con HRP"; derecha tarjeta "producto" (--surface-1 radio 14 --shadow-lg) con vista previa estilizada (mini gráfico de pesos 6 barras + 2 KPIs en SVG con tokens/currentColor). (2) Franja de credibilidad: 3–4 estadísticas en línea con divisores ("30 activos e índices · 2 modelos · 2 fuentes de datos"), cifras 28px tabulares. (3) Módulos: 3 tarjetas clicables (icono 40px en --accent-subtle, título 18px, descripción 2 líneas, 3 capacidades con check, "Abrir módulo →"); quitar numeración 01/02/03. (4) Metodología: 2 columnas HRP | BL con mini diagramas (dendrograma; Prior + Views → Posterior) + nota académica y aviso legal en el pie.
- **Páginas de análisis**: escritorio ≥1100px grid `var(--sidebar) minmax(0,1fr)` gap 24; panel de parámetros a la izquierda sticky top calc(nav+16px), max-height calc(100vh − nav − 32px) con scroll interno y botón primario en pie pegajoso del panel. Resultados: (1) barra de contexto "Resultado HRP · fechas · N activos · Benchmark SPY" + "Descargar informe" a la derecha; (2) KPIs; (3) tarjeta principal "Distribución del portafolio" (gráfico 7/12 + tabla 5/12, apilados <1280px); (4) análisis adicionales en pestañas/secciones; (5) tablas de detalle en acordeones "Ver datos…". Tableta 768–1099: panel arriba ancho completo con fieldsets en 2 columnas, colapsado en <details> tras el cálculo con resumen "Parámetros: 6 activos · desde 2020 · SPY · Editar". Móvil: 1 columna, panel en acordeón (abierto sin resultados), KPIs 2 columnas, gráficos 280px, tablas con scroll horizontal y primera columna sticky, botón primario sticky abajo, objetivos táctiles ≥44px.
- **Monitoreo**: pestañas segmentadas de vista (Portafolio / Opciones / Fundamental) encima del panel; vista Fundamental con cabecera de empresa (nombre, ticker mono, sector badge, precio grande + variación), gráfico Lightweight a ancho completo y retícula de KPIs fundamentales en tarjetas pequeñas.

## Anti-patrones (no hacer)
- Navy+dorado o cualquier dorado/beige; serif display (Playfair).
- Acento como color de serie; verde/rojo financiero o colores de estado como categorías.
- Hex hardcodeados en plantillas, SVG o Python (todo por tokens CSS + módulo de tokens Python).
- Color de serie por posición (debe seguir al ticker). Doble eje Y. Arcoíris en correlación. Punto medio de color en divergente.
- Gráficos PNG; títulos duplicados (en la figura y en el h3).
- Muros de gráficos apilados sin jerarquía (agrupar en pestañas/secciones).
- Dos filas de KPIs con igual peso; cifras no tabulares.
- Párrafos largos de ayuda (usar tooltips); eyebrows largos en mayúsculas del mismo color que el texto.
- Retícula de checkboxes para activos (usar chips con búsqueda y contador).
- Tarjeta-en-tarjeta, sombras grandes, degradados fuertes, glassmorphism decorativo.
- Texto #94a3b8 o #64748b sobre --bg (no pasa AA; mínimo #5f6b7e).
- Quitar outline sin sustituto; comunicar solo con color (negativos con "−" y ▼; avisos con icono).
- Spinner sin contexto durante 15 s.
