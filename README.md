# Atlas · Analítica de portafolios

Herramienta web **educativa** (Flask) para personas jóvenes e inexpertas que quieren aprender a construir portafolios: qué es diversificar, cómo se mide el riesgo y cómo reparten el dinero dos métodos profesionales, Hierarchical Risk Parity (HRP) y Black-Litterman. Cada usuario responde primero un perfil de riesgo y usa las herramientas según su rol (novato, inversionista o gestor). Los precios históricos se descargan de Tiingo (con Yahoo Finance como respaldo) y los datos fundamentales, capitalizaciones y tipos de cambio se consultan en Yahoo Finance.

> **Información educativa. No es recomendación de inversión ni asesoría financiera.**
> Este aviso aparece en la portada y en todas las pantallas con resultados (HRP, Black-Litterman, comparación, Mercado, Mis portafolios, backtests y Mi perfil). Lo genera la macro `educational_notice()` de `web/templates/macros/learning.html`.

## Requisitos

- Python 3.14 o superior
- [uv](https://docs.astral.sh/uv/)

## Instalación y ejecución local

```powershell
uv sync                                    # crea .venv e instala las dependencias de uv.lock
Copy-Item .env.example .env                # y completa FLASK_SECRET_KEY y el correo (MAIL_*)
uv run flask --app wsgi:app run --debug    # servidor de desarrollo
```

Abre `http://127.0.0.1:5000`. Al arrancar se aplican las migraciones pendientes de la base local (`instance/atlas.db`). Para iniciar el servidor local sin el CLI de Flask:

```powershell
uv run optimizacion-portafolios
```

Con Waitress (servidor WSGI de producción, también en Windows):

```powershell
uv run waitress-serve --host 127.0.0.1 --port 8000 wsgi:app
```

### Quién es gestor

El rol `gestor` (acceso total, con la app tal como era antes de la capa educativa) no se puede obtener desde la web ni por configuración: se otorga solo desde la línea de comandos del servidor, a **una sola** cuenta ya registrada:

```powershell
uv run flask --app wsgi:app users make-gestor tu-correo@example.com            # otorga el rol
uv run flask --app wsgi:app users make-gestor otro@example.com --replace       # lo pasa a otra cuenta
uv run flask --app wsgi:app users revoke-gestor tu-correo@example.com          # lo quita (vuelve a novato)
uv run flask --app wsgi:app users list-gestor                                  # muestra quién es gestor
```

El cambio aplica en la siguiente petición, sin reiniciar el servidor.

### Correo: verificación de la cuenta y recuperación de contraseña

Al registrarse, cada cuenta recibe un código de 6 dígitos por correo y no puede entrar hasta escribirlo (`/auth/verificar`). «¿La olvidaste?» (`/auth/recuperar`) envía otro código para crear una contraseña nueva (`/auth/restablecer`). Los códigos vencen a los 15 minutos, sirven una sola vez, se invalidan tras 5 intentos fallidos y solo se guarda su hash (HMAC con `FLASK_SECRET_KEY`). Las respuestas no revelan si un correo tiene cuenta.

Los correos salen por Gmail SMTP. Configura en `.env`:

```dotenv
MAIL_USERNAME=tu-cuenta@gmail.com
MAIL_PASSWORD=xxxx xxxx xxxx xxxx     # contraseña de aplicación, no la normal
# MAIL_FROM=Atlas <tu-cuenta@gmail.com>   (opcional)
# MAIL_SMTP_HOST=smtp.gmail.com  MAIL_SMTP_PORT=465   (valores por defecto)
```

La contraseña de aplicación se crea en tu cuenta de Google → Seguridad → Verificación en dos pasos (debe estar activa) → Contraseñas de aplicaciones. Sin estas variables, el servidor de desarrollo (`--debug`) muestra el código en su consola en lugar de enviarlo; fuera de desarrollo el envío falla con un mensaje.

**Brevo (sin SMTP).** Donde el SMTP saliente está bloqueado (el plan gratis de Render), los correos salen por la API HTTPS de [Brevo](https://www.brevo.com) (gratis hasta 300 correos al día). Si `BREVO_API_KEY` está definida, se usa Brevo en lugar de SMTP:

```dotenv
BREVO_API_KEY=xkeysib-...                # Brevo → SMTP & API → API Keys
MAIL_FROM=Atlas <tu-cuenta@gmail.com>    # obligatorio: remitente verificado en Brevo
```

En Brevo, registra y confirma ese correo en **Senders, Domains & Dedicated IPs → Senders** antes de enviar; si no, Brevo rechaza el envío (el motivo queda en el log del servidor). Con un remitente `@gmail.com` algunos mensajes pueden llegar a spam; un dominio propio autenticado en Brevo lo evita.

## Uso: flujo de pantallas y roles

1. **Sin sesión** solo se ve la portada (`/`): qué es Atlas, para quién es, el aviso educativo y los formularios de inicio de sesión y registro. No muestra herramientas ni resultados (los diagramas de la portada están rotulados como ilustrativos).
2. **Tras iniciar sesión**, si falta el perfil de riesgo la app lleva al cuestionario y luego a confirmar el rol (ver «Perfil de riesgo obligatorio» más abajo).
3. **Pantalla principal** (`/` con sesión): saludo, perfil vigente (perfil final y factor limitante, con enlace a Mi perfil), rol y las herramientas disponibles para ese rol. La barra de navegación muestra solo lo que el rol permite.

| | Novato | Inversionista | Gestor |
|---|---|---|---|
| HRP y Black-Litterman | Vista guiada: solo elige un universo predefinido; explicación de cada peso y glosario | Vista guiada + excluir activos y peso máximo por activo (HRP) | Páginas completas: todos los parámetros, informe QuantStats, explorador de precios |
| Comparar HRP vs Black-Litterman (`/comparar`) | — | Sí | Sí |
| Mis portafolios | Solo lectura: panel, alertas y versiones | Fijar, editar alertas, gestionar (rebalancear, altas, bajas) y archivar | Todo, más señales |
| Mercado (incluida Opciones), `/api/historial`, `/api/ohlc` | — | — | Sí |
| Backtest de HRP e historial de backtests | — | — | Sí |
| Glosario, Mi perfil, Mi historial de decisiones | Sí | Sí | Sí |

- **Valores fijos de la vista guiada** (`web/common/guided.py`): últimos 2 años de precios (la misma ventana de Black-Litterman, para que los dos métodos sean comparables), benchmark S&P 500 (SPY), HRP sin límites (0 %–100 %; el inversionista puede fijar el peso máximo), Black-Litterman con objetivo «Máximo ratio de Sharpe», tasa libre de riesgo 2 % y **views neutrales**: iguales al retorno de equilibrio de cada activo (una primera ejecución del modelo da el equilibrio, que no depende de las views, y la segunda lo usa como view). El modelo Black-Litterman no admite límites de peso, así que el peso máximo solo se aplica a HRP. Universos: «Acciones y Bitcoin», «Sectores de EE. UU.», «Criptomonedas», «Portafolio actual» y, solo en HRP, «Criptomonedas y commodities» (los futuros no tienen capitalización bursátil).
- **Explicación de los pesos:** HRP, a partir de la volatilidad anual de cada activo, su correlación promedio con el resto (de la matriz que usó HRP) y su aporte al riesgo; Black-Litterman, a partir del peso de mercado (capitalización), el retorno de equilibrio y la diferencia entre el peso final y el de mercado.
- **Permisos:** una sola tabla endpoint → roles en `web/auth/permissions.py` (`ENDPOINT_ROLES`, `METHOD_ROLES` para un método concreto, `FEATURE_ROLES` para controles dentro de una página). El access check `require_role` se registra después del de perfil; si el rol no alcanza, redirige a la pantalla principal con «Tu rol (…) no incluye esta función.» (403 JSON en `/api/*`). Un endpoint que no está en la tabla solo lo abre el gestor. Las páginas guiadas leen del formulario solo el universo (y, desde inversionista, los activos marcados y el peso máximo): cualquier otro campo enviado se ignora.

## Configuración de la fuente de precios

Los precios de cierre ajustados se descargan de [Tiingo](https://www.tiingo.com/) cuando la variable de entorno `TIINGO_API_KEY` está definida. Crea una cuenta gratuita, copia tu API key y guárdala en Windows con:

```powershell
setx TIINGO_API_KEY "tu-api-key"
```

`setx` solo afecta a las terminales nuevas: abre otra terminal antes de ejecutar la aplicación.

Yahoo Finance se usa como respaldo:

- Sin `TIINGO_API_KEY`, todos los precios se descargan de Yahoo Finance y se registra una advertencia una sola vez.
- Con la key, se envían a Yahoo Finance los tickers que Tiingo no cubre: acciones colombianas y otros listados fuera de EE. UU. (`.CL` o cualquier sufijo con punto), futuros y divisas (`GC=F`, `EURUSD=X`), índices (`^GSPC`, `^COLCAP`) y cualquier ticker para el que Tiingo responda 404 o no devuelva datos.
- Si Tiingo responde 429 (cupo de solicitudes agotado), ese ticker y los que faltan se descargan de Yahoo Finance.
- Las criptomonedas `XXX-USD` (`BTC-USD`, `ETH-USD`, `BNB-USD`, `XRP-USD`) se piden al endpoint de cripto de Tiingo (`btcusd`, ...) y, si no hay datos, a Yahoo Finance.

Las series de ambas fuentes se alinean por fecha (sin hora ni zona horaria). Los errores HTTP de Tiingo distintos de 404 y 429 (key inválida, fallas del servidor) se informan como error de descarga y no activan el respaldo.

## Usuarios y acceso

Las cuentas son **locales**: viven en la tabla `users` de la base SQLite (ver la sección siguiente). El correo es el usuario (sin distinguir mayúsculas) y la contraseña se guarda solo como hash argon2id (`argon2-cffi`); nunca en texto plano ni en registros.

- **Todo requiere sesión.** Sin iniciar sesión solo se ven la portada (`/`), `/auth/registro`, `/auth/ingresar` y los archivos estáticos. Las páginas redirigen al inicio de sesión (con `next`) y los endpoints JSON (`/api/...`) responden 401. La regla está en `web/auth/guard.py` (`PUBLIC_ENDPOINTS`, `register_access_check`).
- **Registro e inicio de sesión:** contraseña de al menos 8 caracteres con letras y números. Tras 5 intentos fallidos en 15 minutos (por correo o por IP) el acceso se bloquea temporalmente. El mensaje de error no revela si el correo existe.
- **Roles:** `novato` (por defecto), `inversionista` y `gestor`. `gestor` no se puede elegir: se otorga solo con `flask users make-gestor` (ver «Quién es gestor»). Qué ve cada rol: «Uso: flujo de pantallas y roles».
- **Perfil de riesgo obligatorio:** tras el primer inicio de sesión cada usuario responde una sola vez el cuestionario de `config/cuestionario.yaml` (capital disponible en COP, horizonte, 5 preguntas de tolerancia, 3 de capacidad y 2 de conocimiento). Sin perfil vigente ninguna herramienta abre: el access check `require_current_profile` (`web/profile/access.py`) redirige al cuestionario (los `/api/...` responden 403); solo quedan libres la portada, el propio perfilado, cerrar sesión y los estáticos. El progreso se guarda en cada paso (`perfil_borradores`) y se retoma en el siguiente ingreso. En los ingresos siguientes el perfil guardado se carga en `session["perfil"]` (y en la variable de plantilla `current_profile`) y la app va directo al inicio.
  - **Cálculo** (`src/optimizacion_portafolios/profiling/`, sin Flask): cada respuesta vale 1 a 4 según su orden; tolerancia y capacidad se promedian (menos de 2,0 adverso; de 2,0 a menos de 3,0 moderado; 3,0 o más arriesgado). El horizonte pone un tope (menos de 1 año: máximo adverso; 1 a 3 años: máximo moderado; más de 3: sin tope). Perfil final = el más bajo de los tres. Responder «Vendo todo» (pregunta 1) o «Menos de 1» mes de fondo de emergencia (pregunta 6) fuerza adverso. El **factor limitante** es el veto si lo hay; si no, el primero de `veto > horizonte > capacidad > tolerancia` cuyo nivel coincide con el final (el horizonte solo cuenta cuando impone tope). Las preguntas de conocimiento solo sugieren el rol: `inversionista` si la experiencia es «Fondos o acciones» o más y se acierta qué es diversificar; si no, `novato`. Umbrales, vetos, topes, desempate, regla del rol y explicaciones están en el YAML (con `version`), que se valida al arrancar.
  - **Rol:** el usuario confirma (o cambia) el rol sugerido entre `novato` e `inversionista`; hasta confirmarlo las herramientas lo llevan a esa pantalla. A un `gestor` no se le pide confirmar ni se le cambia el rol.
  - **Mi perfil** (`/perfil/`, en el menú de usuario): perfil final, factor limitante con su explicación, fecha e historial. Repetir el cuestionario agrega una fila nueva en `perfiles` y deja solo esa como vigente; las anteriores se conservan.
- **Registro de decisiones** (`/historial`, «Mi historial» en el menú de usuario, todos los roles): cada vez que se genera un portafolio (HRP o Black-Litterman calculado con éxito, en su página o en la comparación, una fila por método) y cada vez que se fija uno, se inserta una fila en la tabla `registro_decisiones` con el usuario, la fecha, el perfil vigente (`perfil_id` y `perfil_final`), el rol, el método, el origen (`hrp`, `black_litterman`, `comparar` o `fijar`), los activos y pesos (JSON) y los parámetros de entrada (universo, fechas, benchmark, límites, objetivo, tasa, views). Es un historial verificable: la app solo inserta y lee (`storage/decisions.py`); el modelo rechaza cualquier actualización o borrado desde el ORM. Descargar el informe QuantStats no cuenta como decisión.
- **Historial de backtests:** cada backtest HRP se guarda una vez por combinación de activos, periodo y parámetros (`/cuenta/backtests`, solo gestor), en la base local.
- **Migrar portafolios de las antiguas cuentas de InsForge:** registra la cuenta local y ejecuta `flask --app wsgi:app users reassign-portfolios <id_insforge> <correo>`.
- **Traer el historial de backtests guardado en InsForge:** `flask --app wsgi:app users import-insforge-backtests [--dry-run] [--fallback <correo>]`. Cada backtest va a la cuenta local con el mismo correo que su dueño en InsForge (necesita `INSFORGE_URL` e `INSFORGE_API_KEY`); los repetidos se omiten y `--fallback` asigna los que no tienen cuenta local.

Configuración local: copia `.env.example` a `.env` y completa las variables. Define `FLASK_SECRET_KEY` (si falta, la app avisa en el log y las sesiones se pierden al reiniciar).

### Caché en memoria (equivalente a `st.cache_data`)

Cada proceso guarda en memoria (`data/cache.py`) lo que ya descargó, con un tiempo de vida por entrada contado desde la descarga: precios y OHLCV por `(tickers, inicio, fin)` 15 minutos (64 entradas), perfiles de Yahoo 15 minutos, tipos de cambio (`fx.usd_rate` y el de las capitalizaciones) 15 minutos y la moneda de cada ticker 12 horas. Los resultados vacíos (ticker sin datos o fallo pasajero del proveedor) se guardan solo 60 segundos, y los errores nunca se guardan. Cada llamada devuelve una copia independiente. En HRP con backtest, los activos se descargan una sola vez desde el inicio de la ventana de estimación y la optimización reutiliza ese tramo; al agregar activos a un portafolio, la validación descarga ya todos los activos de la nueva versión.

### Caché de precios (InsForge, opcional)

[InsForge](https://insforge.dev) ya no gestiona cuentas: solo aporta, si se configuran `INSFORGE_URL` e `INSFORGE_API_KEY`, una caché persistente de precios en Postgres. Los cierres ajustados se guardan en `price_history` y se reutilizan; al optimizar solo se descargan los días que faltan desde el último guardado (más 7 días de solapamiento). Si el solapamiento no coincide con lo guardado (un dividendo o split reajustó la serie), o si la última descarga completa tiene más de 7 días (`ATLAS_PRICE_FULL_REFRESH_DAYS`), se vuelve a descargar el historial completo de ese activo. Un rango que llega a hoy se considera vigente durante 60 minutos (`ATLAS_PRICE_INTRADAY_TTL_MINUTES`), porque el precio del día puede seguir cambiando. Si InsForge falla o sus consultas de una misma descarga suman más de 10 segundos (`ATLAS_PRICE_STORE_BUDGET_SECONDS`; el tiempo de descarga de los proveedores no cuenta), los precios se descargan directamente y la caché se omite durante los 10 minutos siguientes. `INSFORGE_API_KEY` es una clave de administrador: solo la usa el servidor y nunca debe subirse al repositorio. El esquema de esas tablas está en `migrations/` (`npx -y @insforge/cli db migrations up --all`).

## Portafolios en seguimiento (base de datos local)

Los portafolios fijados como óptimos, sus versiones, posiciones y operaciones se guardan en una base SQL local con Flask-SQLAlchemy:

- **Ubicación:** `DATABASE_URL` (por defecto `sqlite:///instance/atlas.db`; la carpeta `instance/` está fuera del control de versiones). `ATLAS_INSTANCE_PATH` cambia la carpeta `instance/`.
- **Migraciones:** son revisiones de Flask-Migrate en `src/optimizacion_portafolios/storage/migrations`. La app aplica las pendientes al arrancar; se desactiva con `ATLAS_AUTO_MIGRATE=0`. Para hacerlo a mano:

  ```powershell
  uv run flask --app wsgi:app db upgrade --directory src/optimizacion_portafolios/storage/migrations
  ```

  Tras cambiar `storage/models.py`, genera la revisión con `db migrate -m "..."` y el mismo `--directory`.
- **Fijar un portafolio óptimo:** en los resultados de HRP y Black-Litterman, con la sesión iniciada, el botón «Establecer como portafolio óptimo» pide nombre, capital nocional (100 000 USD por defecto), benchmark y comisión (10 pb por defecto). Se guardan el modelo, todos sus parámetros (universo, fechas, límites de peso, views, objetivo, tasa libre de riesgo), los pesos objetivo, los últimos precios de cierre y las unidades (capital neto de comisión × peso / precio). La fecha de esos precios es el inicio del seguimiento.
- **Valoración:** comprar y mantener en USD con las unidades vigentes. Los cierres son ajustados, así que los dividendos se reinvierten implícitamente; un activo que deja de cotizar conserva su último precio. Los precios en otra moneda se convierten con el tipo de cambio diario de Yahoo (`{CCY}USD=X`); los precios en peniques (`GBp`) se dividen entre 100.
- **Métricas de seguimiento** (`analytics/tracking.py`, días hábiles, 252 por año): retorno acumulado, del mes, del año y del último año (N/D hasta tener un año), CAGR y contribución al retorno por activo; volatilidad anualizada y móvil (21 y 63 días), drawdown actual, máximo y su duración, VaR y CVaR históricos al 95 % (diarios), semidesviación y contribución al riesgo; Sharpe, Sortino (QuantStats), Calmar y Sharpe móvil de 126 días; beta, alpha de Jensen, tracking error, information ratio y capturas alcista y bajista frente al benchmark; desviación de pesos, índice Herfindahl, número efectivo de activos, correlación promedio móvil (63 días) y rotación acumulada. La tasa libre de riesgo es la del modelo (Black-Litterman) o 0.
- **Fotos diarias (snapshots):** cada día valorado guarda valor, valor del benchmark, pesos y, el último día, todas las métricas (`DailySnapshot`). Los días pasados no se reescriben aunque el proveedor revise sus precios; solo se actualiza la foto del último día. Al abrir el panel se guardan las fotos que falten.

  ```powershell
  uv run flask --app wsgi:app portfolios snapshot            # hasta hoy
  uv run flask --app wsgi:app portfolios snapshot --date 2026-10-16
  ```

  El comando termina con código 1 si algún portafolio falla (el resto se guarda igual).

  **Programarlo en Windows (Programador de tareas):** abre «Programador de tareas» → «Crear tarea básica…», nombre `Atlas snapshot`, desencadenador «Diariamente» a las 18:30 (después del cierre de EE. UU.), acción «Iniciar un programa» con:

  - Programa: `powershell.exe`
  - Argumentos: `-NoProfile -Command "uv run flask --app wsgi:app portfolios snapshot *>> instance\snapshot.log"`
  - Iniciar en: la carpeta del proyecto (donde están `wsgi.py` y `.env`).

  O con PowerShell, desde la carpeta del proyecto:

  ```powershell
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory (Get-Location) `
    -Argument '-NoProfile -Command "uv run flask --app wsgi:app portfolios snapshot *>> instance\snapshot.log"'
  $trigger = New-ScheduledTaskTrigger -Daily -At 6:30pm
  Register-ScheduledTask -TaskName "Atlas snapshot" -Action $action -Trigger $trigger
  ```

- **Alertas** (`analytics/alerts.py`): reglas por portafolio con semáforo verde/amarillo/rojo, editables al fijar el portafolio y en `/portafolios/<id>/alertas`. Un nivel se alcanza cuando el valor supera estrictamente su umbral:

  | Regla | Amarillo | Rojo |
  |---|---|---|
  | Drawdown actual | > 10 % | > 15 % |
  | Volatilidad móvil de 21 días frente a su promedio desde la fijación | > 1,5× | > 2× |
  | Mayor desviación de un peso frente al objetivo | > 5 pp | > 10 pp |
  | Días seguidos con Sharpe móvil de 126 días negativo | > 63 | > 126 |
  | Mayor participación de un activo en el riesgo total | > 30 % | > 40 % |
  | Correlación promedio móvil de 63 días | > 0,70 | > 0,85 |
  | Días hábiles sin precio nuevo de un activo (posible baja de cotización) | > 2 | > 5 |

  Las reglas se evalúan al abrir el panel y con `flask portfolios snapshot`. Cada evento guarda cuándo se disparó y cuándo se resolvió; un cambio de nivel cierra el evento anterior y abre uno nuevo. No se envían correos ni notificaciones.
- **Gestión** (`/portafolios/<id>/gestionar`, lógica en `models/rebalancing.py`): cada cambio crea una versión nueva con motivo, fecha, pesos, unidades, operaciones y costo, sin reiniciar el seguimiento.
  - **Rebalancear:** a los pesos objetivo vigentes o re-optimizando con el mismo modelo (HRP con la misma longitud de ventana; Black-Litterman con sus views, objetivo y tasa libre de riesgo) y datos hasta hoy.
  - **Eliminar activos:** el peso liberado se reparte en proporción o re-optimizando con los activos restantes.
  - **Agregar activos:** con peso manual (el resto se reduce en proporción) o re-optimizando con el nuevo universo (en Black-Litterman hay que indicar la view del activo nuevo). El ticker se valida descargando sus precios.
  - **Vista previa:** antes de confirmar se muestran los pesos antes y después, las compras y ventas, el turnover (de una vía: la mitad de lo operado sobre el valor) y el costo (comisión del portafolio en pb sobre cada monto operado). La vista previa queda firmada; si el portafolio cambia antes de confirmar, se rechaza.
  - Se respetan los límites de peso de HRP (`WeightBounds`) en todas las operaciones; si no existe una asignación que los cumpla, el cambio se rechaza con un mensaje.
  - Las operaciones usan el último cierre disponible. Si el mercado está abierto, ese precio todavía puede moverse y la valoración del día se ajustará al cierre.
  - El valor es continuo: el día del cambio, el portafolio vale lo que valía antes menos el costo.
  - **Versiones:** historial en `/portafolios/<id>/versiones`, con comparación de dos versiones (`?a=1&b=3`).
  - **Archivar:** el portafolio deja de monitorearse (sin snapshots ni alertas) y conserva todo su historial.
- **Señales** (`/portafolios/<id>/senales`, `models/signals.py`): reglas por activo simuladas con vectorbt (`from_signals` con `group_by` y `cash_sharing`) sobre los pesos vigentes desde la fecha de fijación, frente a comprar y mantener. Un activo está «dentro» mientras se cumplen las reglas activas: media rápida sobre la lenta (50/200 días), volatilidad de 21 días como máximo 2× su mediana y drawdown del activo menor al 20 %. Aparte, un stop-loss del 10 % (dinámico por defecto) cierra la posición. Cada entrada compra peso objetivo × capital, porque el simulador de señales de vectorbt no admite tamaños de porcentaje objetivo. Son sugerencias: nunca modifican el portafolio; para actuar hay que usar Gestionar, que pide confirmación. La primera simulación tarda más mientras numba compila.
- **Usuarios:** son las cuentas locales (`users`). Flask-Login gestiona la sesión (`current_user`) y cada fila guarda `str(users.id)` en `user_id`; un portafolio ajeno responde 404.

## Pruebas

```powershell
uv run pytest -q
```

`uv run python -m unittest discover -s tests` ejecuta la misma suite sin pytest.

Las pruebas no acceden a la red: las descargas de Tiingo y Yahoo Finance se simulan con `unittest.mock`.

## Despliegue WSGI

Waitress está incluido para ejecutar Flask en Windows y otros sistemas (así escucha en todas las interfaces de red):

```powershell
uv run waitress-serve --host 0.0.0.0 --port 8000 wsgi:app
```

Configura `FLASK_SECRET_KEY` con un valor aleatorio secreto al desplegar y habilita `FLASK_SESSION_COOKIE_SECURE=true` cuando la aplicación se sirva exclusivamente por HTTPS. El servidor Flask integrado es para desarrollo; Waitress es el servidor WSGI de producción.

## Despliegue en Render (imagen Docker)

El `Dockerfile` construye una imagen con Python 3.14, las dependencias de `uv.lock` y Waitress; al arrancar aplica las migraciones y escucha en `$PORT`. `render.yaml` es un Blueprint de Render que crea el servicio web (runtime Docker) y una base Postgres, y conecta las dos con `DATABASE_URL`.

1. Sube la rama a GitHub. En Render: **New → Blueprint**, elige este repositorio y la rama.
2. Render pide los valores marcados `sync: false`: `BREVO_API_KEY` y `MAIL_FROM` (correo con Brevo, ver «Correo»), `INSFORGE_URL`, `INSFORGE_API_KEY` y `TIINGO_API_KEY` (los que no uses quedan vacíos). `FLASK_SECRET_KEY` se genera solo.
3. Al terminar el despliegue, la app queda en `https://atlas-xxxx.onrender.com`, accesible desde cualquier dispositivo. La base empieza vacía: cada persona se registra de nuevo.
4. Para otorgar el rol gestor, abre la **Shell** del servicio en Render y ejecuta `flask --app wsgi:app users make-gestor tu-correo@example.com`.

Variables propias del despliegue (ya definidas en `render.yaml`):

- `DATABASE_URL`: Render entrega `postgres://…`; la app lo convierte para usar el driver `psycopg` (incluido en las dependencias).
- `ATLAS_PROXY_HOPS=1`: número de proxies de confianza delante de la app. Con él, la IP del cliente (usada por el límite de intentos de inicio de sesión) y el esquema HTTPS se leen de `X-Forwarded-For`/`X-Forwarded-Proto`. No lo definas si la app recibe las peticiones directamente: cualquiera podría falsear su IP.
- `FLASK_SESSION_COOKIE_SECURE=true`: cookies solo por HTTPS.
- `ATLAS_VECTORBT_WARMUP=0`: no precompila vectorbt al arrancar, para quedar dentro de los 512 MB del plan gratis (la app usa unos 330 MB sin el precalentamiento y unos 420 MB con él). El primer backtest tarda unos segundos más.

Limitaciones del plan gratis de Render:

- **El servicio se duerme** tras 15 minutos sin visitas; la siguiente visita tarda cerca de un minuto en despertarlo.
- **La base Postgres gratis vence a los 30 días**; para conservar los datos hay que pasarla a un plan pago antes.
- **Render bloquea el SMTP saliente** (puertos 25, 465 y 587) en los servicios gratis; por eso `render.yaml` envía los códigos con Brevo (`BREVO_API_KEY`). Sin esa clave nadie puede terminar el registro.

Para probar la imagen en local (requiere Docker):

```powershell
docker build -t atlas .
docker run -p 8000:8000 --env-file .env atlas
```

El workflow `.github/workflows/docker-image.yml` construye la imagen y comprueba que la portada responda en cada pull request.

## Módulos

- **Monitoreo de activos:** universos de opciones y portafolios, tickers personalizados, fechas, precios ajustados, retornos, retornos anualizados, riesgo, volatilidad histórica por periodo y análisis fundamental.
- **Optimización HRP** ([skfolio](https://skfolio.org/) `HierarchicalRiskParity`, enlace *single*): selección de activos, benchmark, límites de peso mínimo y máximo (globales y por activo), pesos, correlación cuasi-diagonal, contribuciones, ratio de Sortino, métricas de riesgo, gráficos QuantStats e informe HTML descargable.
- **Black-Litterman** (skfolio `BlackLitterman` + `MeanRisk`): prior de equilibrio por capitalización bursátil en USD, views anuales por activo editables en la interfaz con confianza fija del 95 % (método de Idzorek), objetivos de optimización (máximo Sharpe, máximo Sortino, mínima varianza, mínimo CVaR, máxima rentabilidad, máxima utilidad), retornos prior y posterior, covarianza posterior, ratio de Sortino esperado y análisis/informe QuantStats histórico.

## Arquitectura

El código está organizado en capas. Cada capa solo depende de las que están por debajo:

```text
web        →  interfaz Flask: rutas HTTP, servicios de página, plantillas
models     →  modelos de optimización y pronóstico (HRP, Black-Litterman, ARIMA)
analytics  →  estadísticas, volatilidad y métricas de desempeño
data       →  catálogos de activos y acceso a Tiingo/Yahoo Finance
```

`data`, `analytics`, `models` y `profiling` no importan Flask: se pueden usar desde un notebook, un script o las pruebas sin levantar la aplicación.

```text
wsgi.py                         punto de entrada WSGI (Flask CLI y Waitress)
config/cuestionario.yaml        preguntas, opciones, umbrales, vetos y reglas del perfil de riesgo
tests/
  support.py                    precios sintéticos y cliente Flask con CSRF
  test_market_data.py           capa data
  test_analytics.py             capa analytics
  test_hrp_model.py             modelo HRP
  test_black_litterman_model.py modelo Black-Litterman
  test_web.py                   rutas HTTP de punta a punta
  golden_cases.py, test_golden.py, golden/values.json
                                red de seguridad numérica: salidas fijadas de modelos, analytics y servicios
scripts/
  measure_pages.py              tiempos de carga de las páginas principales (frío/caliente, datos reales)
src/optimizacion_portafolios/
  data/
    catalogs.py                 universos, benchmarks y periodos de volatilidad
    market_data.py              precios (Tiingo + respaldo Yahoo) y perfiles con caché
    cache.py                    cachés en memoria con tiempo de vida por entrada
    tiingo_prices.py            cliente Tiingo y enrutamiento de tickers por fuente
    fx.py                       moneda de cada ticker y conversión diaria a USD
  analytics/
    tracking.py                 seguimiento de portafolios fijados (funciones puras)
    alerts.py                   reglas de alerta y su evaluación (funciones puras)
    statistics.py               retornos, riesgo y retornos esperados
    volatility.py               volatilidad mensual e histórica por periodo
    performance.py              métricas e informe QuantStats
  models/
    hrp.py                      pesos HRP y contribuciones
    rebalancing.py              cambios de portafolio: límites, operaciones, costo y turnover
    signals.py                  señales de entrada y salida por activo (vectorbt)
    black_litterman.py          prior de equilibrio, views y optimización
    arima.py                    flujo Box-Jenkins (pendiente de integrar en la web)
  web/
    __init__.py                 create_app(), CSRF, manejo de errores, run()
    templates/  static/         plantilla base, error y CSS compartidos
    common/
      charts.py                 gráficos interactivos Plotly (JSON para el navegador)
      tables.py                 tablas HTML escapadas y formatos
      forms.py                  lectura y validación de formularios
      errors.py                 mensajes de fallos de descarga (límite de Yahoo incluido)
      signing.py                tokens firmados (itsdangerous) de resultados y cambios
    main/                       portada (sin sesión), pantalla principal por rol y glosario
    compare/                    comparación HRP vs Black-Litterman (inversionista y gestor)
    decisions/                  registro de decisiones: Mi historial y el enlace con las páginas de resultados
    monitoring/                 monitoreo y análisis fundamental
    hrp/                        optimización HRP
    black_litterman/            optimización Black-Litterman
    auth/                       cuentas locales: registro, sesión (Flask-Login), roles y permisos por rol, guardia global, límite de intentos
    profile/                    perfil de riesgo: cuestionario por pasos, resultado, confirmación de rol, Mi perfil
    account/                    historial de backtests HRP
    portfolios/                 Mis portafolios: seguimiento de portafolios óptimos
  profiling/                    cuestionario de perfilado (carga y validación del YAML) y cálculo del perfil (sin Flask)
  storage/
    decisions.py                registro de decisiones (solo inserciones y lecturas)
    profiles.py                 perfiles de riesgo (una sola fila vigente por usuario) y borradores del cuestionario
    models.py                   tablas SQLAlchemy (User, Profile, ProfileDraft, DecisionRecord, BacktestRun, BacktestResult, Portfolio, PortfolioVersion, Holding, Transaction, DailySnapshot, AlertRule, AlertEvent)
    repositories.py             lecturas y escrituras filtradas por usuario
    users.py                    cuentas locales y reasignación de dueño
    migrations/                 revisiones de Flask-Migrate
```

Cada módulo web (`main`, `monitoring`, `hrp`, `black_litterman`) sigue la misma forma:

| Archivo | Responsabilidad |
|---|---|
| `__init__.py` | Declara el `Blueprint` |
| `routes.py` | Solo HTTP: lee el formulario, valida, muestra mensajes y renderiza |
| `services.py` | Orquesta datos → analytics → modelo y arma el diccionario que usa la plantilla |
| `templates/<módulo>/` | Plantillas Jinja del módulo |

## Convenciones para extender el proyecto

- **Nuevo cálculo o modelo:** va en `analytics/` o `models/`, sin importar Flask, con sus pruebas en `tests/test_analytics.py` o `tests/test_<modelo>_model.py`.
- **Nueva fuente de datos:** va en `data/`. Las llamadas de red se simulan en las pruebas.
- **Nueva página:** crea `web/<módulo>/` con `__init__.py`, `routes.py`, `services.py` y `templates/<módulo>/`, y registra el Blueprint en `web/__init__.py`.
- **Gráficos y tablas:** reutiliza `web/common/charts.py` y `web/common/tables.py` antes de crear funciones nuevas.
- **Nuevos universos de activos:** se agregan en `data/catalogs.py`.
- **Idioma:** el código nuevo usa identificadores en inglés y textos de interfaz en español. `models/black_litterman.py` y `models/arima.py` conservan su API original en español.
- **Integración de ARIMA:** cuando se incorpore, crea `web/forecast/` siguiendo la misma forma y reutiliza `models/arima.py`.
