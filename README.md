# cc-dash

Dashboard en vivo, en la terminal, de las sesiones de Claude Code: qué sesiones
están abiertas, qué modelo lleva cada una, herramientas en curso, subagentes,
tokens y un log cronológico. Soporta varias cuentas en la misma máquina.

Solo lee archivos locales de Claude Code y no envía nada. Lo único que escribe
fuera de su configuración es el hook opcional de `cc-dash hook install`.

## Instalar

Requiere Python 3.11+ en Linux o macOS (Windows: sin probar).

```sh
git clone <url-del-repo> cc-dash   # o copia la carpeta
cd cc-dash
./install.sh
```

`install.sh` usa `uv` o `pipx` si existen; si no, crea un entorno virtual en
`~/.local/share/cc-dash` y enlaza `~/.local/bin/cc-dash`. Para quitarlo:
`./install.sh --uninstall`.

## Actualizar

Si se instaló desde un clon de git, al abrir el dashboard `cc-dash` comprueba
(como mucho cada 6 horas) si el repo tiene cambios; si los hay, hace
`git pull --ff-only`, se reinstala y arranca ya con la versión nueva.

```sh
cc-dash update          # comprobar y actualizar ahora
cc-dash --no-update     # abrir sin comprobar
```

Para desactivarlo, pon `auto_update = false` en la configuración.

## Usar

```sh
cc-dash                 # todas las cuentas
cc-dash -a personal     # solo una cuenta (repetible)
cc-dash --once          # imprime un fotograma y sale
```

Dentro del dashboard, **`?`** abre una ventana flotante con todas las teclas,
agrupadas por tema; cualquier tecla la cierra. La barra inferior muestra solo
el estado de la sesión enfocada y los atajos esenciales (`? ayuda · , config ·
q salir`), más los propios del modo en que estés.

**Paneles:** `Tab` pasa el foco del teclado al panel siguiente (`Shift+Tab` al
anterior), en este orden: sesiones, agentes, session log y panel derecho. El
panel con el foco lleva el borde en color. Dentro de él, `j`/`k` (o las
flechas) se mueven, `gg` va al primero y `G` al último:

- **sesiones:** cambia de sesión; `Enter` salta a su panel de tmux.
- **agentes:** elige subagente (al entrar se elige el primero); `Enter` activa
  el modo foco sobre él.
- **session log:** mueve el cursor por las entradas. `Enter` (o espacio)
  abre el detalle de la entrada en una ventana flotante: si es un `.md` que la
  sesión escribió, editó o leyó, lo abre con el visor configurado (`glow -p`
  en un popup de tmux); si no, en una ventana dentro del dashboard con la
  entrada, la salida y los parámetros completos (`j`/`k` desplazan, `y` copia,
  `Esc` o `Enter` cierran). El panel derecho ya no cambia solo al mover el
  cursor.
- **panel derecho:** en la actividad, un cursor recorre las líneas. `Enter`
  despliega o pliega la lista bajo el cursor (herramientas, archivos,
  skills…) para verla entera. `e` sobre un archivo (editado o leído) lo abre
  en un popup entero, con colores de sintaxis y lo que cambió según git
  resaltado: las líneas nuevas sobre verde, las cambiadas sobre ámbar y las
  borradas en rojo donde estaban. Cada bloque de cambios empieza con `◆`; el
  visor arranca en el primero, y `n`/`N` saltan al siguiente o al anterior.
  Contra qué se compara:
  - si tiene cambios sin commitear, contra el último commit;
  - si es nuevo y git no lo sigue, todo cuenta como añadido;
  - si no tiene cambios pendientes, el último commit que lo tocó (útil si la
    sesión ya commiteó).

  La cabecera dice cuál de los tres casos es y cuántas líneas se añadieron,
  cambiaron y borraron. Fuera de un repositorio git muestra el archivo con
  colores, sin marcas. También desde la terminal: `cc-dash changes ARCHIVO`. En respuestas, prompts, tareas o novedades, `j`/`k`
  desplazan el texto.

Las principales:

- `↑`/`↓` o `j`/`k`: cambia de sesión · `a`: vuelve al modo automático
- `←`/`→`: selecciona un subagente · `r`/`p`: su respuesta o su prompt
- `F`: foco en el subagente seleccionado (ver abajo)
- `s`: cursor en el session log; `Enter` abre el detalle de la entrada en una ventana flotante
- `o`: tareas · `n`: novedades · `v`: conversación de la sesión
- `D`: abre el último `.md` que escribió la sesión (un spec, un plan…)
- `d`: diseño · `T`: tema · `,`: configuración · `q`: sale

`d` alterna el panel de agentes entre cinco diseños: `lista`, `tabla`,
`tarjetas`, `tiempo` y `diagrama`. En `diagrama`, sin flechas, el lead ocupa
la primera fila y debajo va una fila por subagente. Cada fila tiene el rostro
cyberpunk del agente (calavera, oni, visor, respirador, kitsune o cíclope,
según su tipo, en puntos de braille tramados), y a su lado quién es, cuánto
lleva, qué hace y lo último que dijo, usando todo el ancho de la fila. Las
filas de los subagentes cuelgan un poco a la derecha del lead, unidas por una
guía tenue, y se encogen para que quepan todos; si no caben, se recorren con
`j`/`k` o `←`/`→`. Lo que va haciendo cada agente se lee en el session log,
donde sus entradas llevan el color del agente. Mientras trabaja, los ojos del
rostro laten; al terminar se apaga, y si falla se agrieta en rojo.

Cada subagente tiene un color propio, por orden de creación, que se repite en
su tarjeta o fila en todos los diseños y en el gráfico de actividad.

El panel a la derecha del log muestra el subagente seleccionado (respuesta o
prompt) o, si no hay ninguno, la actividad de la sesión: porcentaje de la
ventana de contexto, tokens y caché, turnos y git, en filas de estado; debajo,
como listas de una entrada por línea con su cuenta y una barra proporcional,
las herramientas usadas (y cuántas fallaron), skills, archivos editados (con la
ruta relativa a la carpeta de la sesión), servidores MCP, subagentes por tipo
y hooks. Si no cabe, `AvPág`/`RePág` lo desplazan.

La ventana de contexto no figura en los transcripts: se asume 1M si el modelo
lo indica (`[1m]`) o si el contexto ya pasa de 200k, y 200k en otro caso.

## HP, nivel y experiencia

Cada agente (el lead y cada subagente) tiene estadísticas de juego, que en el
diseño `diagrama` van debajo de su rostro:

- **HP**: el contexto libre. Baja a medida que el agente llena su ventana de
  contexto; se pone ámbar por debajo de 50 %, rojo por debajo de 20 % y
  parpadea por debajo de 10 %.
- **Rondas**: las del lead son sus turnos; las de un subagente, sus llamadas
  al modelo.
- **LV**: cada 10 rondas sube un nivel. La barra de LV muestra cuánto falta
  para la próxima evolución.
- **XP**: las rondas del nivel actual, de 0 a 10 (`XP ▰▰▰▱▱▱▱▱ 3/10`).
- **Evolución**: al subir suficientes niveles, el rostro evoluciona. Cuándo y
  qué gana es una sorpresa; cada evolución se anuncia con un toast.

**`L`** abre la ventana de evoluciones: un rostro por vez, en grande y
animado. `h`/`l` cambian de personaje, `j`/`k` bajan o suben de evolución, y
la escalera dice qué gana el rostro en cada una. Las evoluciones que ningún
subagente de la sesión ha alcanzado salen como `???` y no se pueden ver. Abajo indica qué tipos de subagente de la sesión llevan ese rostro.
Si hay un subagente seleccionado, la ventana abre en su rostro y su evolución.

El número exacto de rondas aparece junto al agente (`23 rondas`) y en el
panel de actividad. Cuándo evoluciona cada rostro es una sorpresa. Los
demás diseños muestran HP, LV y XP en la cabecera del lead. Si no caben las
tres barras, se resumen en una o dos líneas.

## Foco en un subagente

`F` centra el dashboard en un subagente: el seleccionado o, si no hay ninguno,
el primero a la vista.

- La cabecera muestra `◎ FOCO <TIPO>` en el color del agente.
- El session log muestra solo sus entradas, y `/` busca dentro de ellas.
- El panel de la derecha pasa a su propia actividad: estado, modelo, tiempo,
  qué hace ahora, ritmo, errores, y las listas de herramientas, skills,
  archivos editados y leídos y MCP de ese agente. `r` y `p` cambian a su
  respuesta y su prompt; `i` vuelve a la actividad.
- En el gráfico de actividad, el agente enfocado va abajo y en su color, y
  el resto queda apagado; el ritmo de la leyenda es solo el suyo.

`←`/`→` pasan el foco al subagente siguiente o anterior. `F` o `Esc` salen del
foco, igual que cambiar de sesión (`j`/`k`), volver al modo automático (`a`) o
cambiar de cuenta (`c`).

## Saltar a una sesión, avisos, pulso y temas

- **`Enter`** lleva tu cliente de tmux al panel donde corre la sesión
  enfocada. Hace falta que tanto `cc-dash` como la sesión estén dentro de tmux
  en la misma máquina.
- **Avisos:** cuando una sesión pasa a esperarte, cuando termina tras más de
  un minuto de trabajo o cuando falla un subagente, suena el pitido de la
  terminal, aparece un mensaje en la barra de tmux y queda una línea `aviso`
  en el log. `m` los silencia (`alerts = false` en la configuración).
- **Actividad:** al pie del panel de agentes, en todos los diseños, un
  gráfico de matriz de puntos con las herramientas que usó la sesión enfocada
  en los últimos 10 minutos, una columna por intervalo de unos segundos y una
  marca en la base cada 2 minutos. Las columnas se apilan por quién las usó: el
  lead en naranja y cada subagente en su color. Cuanto más uso, más intenso el
  color.
  La barra de ahora destella con cada herramienta nueva, y el rombo de la
  leyenda late más rápido cuanto más ritmo hay. La leyenda muestra cuántas
  herramientas aportó cada uno, más las herramientas y los tokens por minuto.
- **Temas:** `T` los recorre; también `cc-dash --theme neon`.
  - `clasico`, `neon`, `matrix` y `ambar`. Los tres últimos usan un marco
    tipo HUD (solo esquinas).
  - `catppuccin-mocha`, `catppuccin-macchiato`, `catppuccin-frappe` y
    `catppuccin-latte`: la paleta [Catppuccin](https://catppuccin.com), con
    los subagentes en sus colores de acento. `latte` es para terminales con
    fondo claro.
  - `terminal`: usa los 16 colores de tu terminal, así sigue el esquema que
    tengas configurado en el shell (el que sea). Como esos colores no se
    pueden mezclar, los fundidos y el "respirar" de los colores pasan de un
    tono a otro en vez de transicionar suave.

## Detector de atascos y ritmo de consumo

Mientras una sesión trabaja, el dashboard la marca con `⚠` en rojo (cabecera,
lista de sesiones, panel del lead y actividad) y lanza un aviso si:

- acumula 3 resultados con error seguidos,
- repite 4 veces seguidas la misma llamada con los mismos parámetros,
- una herramienta lleva más de 5 minutos sin volver, o
- lleva más de 3 minutos sin escribir nada en el transcript.

Un subagente en marcha que lleva más de 5 minutos sin actividad se marca igual.
Son sospechas, no certezas: un comando largo legítimo también dispara la
tercera. Los umbrales se ajustan con `cc-dash set` (ver más abajo).

El ritmo de consumo aparece junto al pulso (`tok/min`, media de los últimos 5
minutos) y en el panel de actividad: tokens de salida de hoy en la sesión y en
la cuenta, y cuántos turnos faltan para llenar el contexto al crecimiento
medio de los últimos turnos. Solo cuenta tokens de salida, y los de los
subagentes quedan fuera porque sus transcripts no los registran bien.

## Tareas, git, buscar y copiar

- **Tareas:** si la sesión lleva una lista de tareas, la cabecera del lead y
  el panel de actividad muestran una barra de progreso (`▰▰▰▱▱ 1/4`) y la
  tarea en curso.
- **`o`** cambia el panel de actividad por las tareas de la sesión enfocada,
  en dos bloques:
  - **En segundo plano:** los subagentes y los comandos que quedaron corriendo
    en segundo plano, con su estado (en marcha, `✓` terminada, `✗` fallida) y
    su duración.
  - **Pendientes:** la lista que escribe el propio agente (`✓` hechas, `▶` en
    curso, `☐` pendientes). Solo aparece si el agente de esa sesión tiene las
    herramientas de lista de tareas; muchas sesiones no la llevan.

  `AvPág`/`RePág` desplazan el panel si no cabe.
- **Git:** el panel de actividad muestra, para la carpeta de la sesión
  enfocada, la rama, los archivos modificados y sin seguimiento, y los commits
  hechos desde que empezó la sesión. Se consulta cada 20 segundos, sin
  escribir nada en el repositorio.
- **`/`** filtra el log por texto mientras escribes: `Enter` deja el filtro
  puesto y `Esc` lo borra.
- **`y`** copia al portapapeles lo que tienes delante: la salida de la entrada
  elegida en el log, la respuesta o el prompt del subagente seleccionado, o la
  última respuesta de la conversación. Dentro de tmux queda además en su búfer.

## Efectos

`E` los activa o apaga todos (`effects = false` en la configuración):

- una secuencia de arranque de un segundo con lo que se cargó,
- un parpadeo "glitch" en la cabecera cuando salta un aviso, y
- lluvia de caracteres en el panel de agentes cuando la sesión enfocada lleva
  más de un minuto sin trabajar y no tiene subagentes a la vista.

## Novedades: toasts y registro

Cada novedad aparece como un toast en la esquina superior derecha, con una
barra que se acorta hasta que desaparece a los 8 segundos (caben 4 a la vez):

- una sesión pasa a esperarte, parece atascada o termina un turno largo,
- un subagente termina o falla,
- una tarea de la lista se completa.

`x` cierra todos los toasts. `n` cambia el panel de actividad por el registro
de novedades, con la hora de cada una (`RePág` va hacia atrás). El pitido y el
mensaje de tmux quedan solo para las importantes (espera, atasco, fallo y
turno terminado); `m` los silencia sin quitar los toasts.

Cuando una tarea se completa, además del toast: en la lista (`o`) un barrido
de color recorre la fila y queda brillando unos segundos antes de pasar a
tachada, y en la cabecera del lead la barra de progreso brilla y anuncia
`✦ terminada: …`.

## Umbrales y opciones

```sh
cc-dash set                    # lista claves, valores actuales y qué hacen
cc-dash set stuck_tool 900     # una herramienta puede tardar 15 minutos
cc-dash set stuck_silent 0     # 0 desactiva esa señal de atasco
cc-dash set toasts false       # sin toasts (igual que la tecla X)
```

| Clave | Por defecto | Qué controla |
| --- | --- | --- |
| `stuck_errors` | 3 | errores seguidos para sospechar un atasco |
| `stuck_repeat` | 4 | veces seguidas la misma llamada |
| `stuck_tool` | 300 | segundos que una herramienta puede tardar en volver |
| `stuck_silent` | 180 | segundos trabajando sin actividad |
| `hide_after` | 60 | segundos que un subagente terminado sigue a la vista |
| `toast_seconds` | 8 | segundos que dura un toast |
| `toast_max` | 4 | toasts visibles a la vez |
| `toasts`, `alerts`, `effects`, `auto_update` | true | interruptores |
| `viewer`, `viewer_popup`, `spec_match` | — | visor de documentos (ver más abajo) |

Dentro del dashboard, **`,`** abre una ventana flotante con todos estos
ajustes más el tema y el diseño: `j`/`k` mueve, `h`/`l` cambia el valor,
`Enter` alterna, `r` vuelve al valor por defecto y `Esc` cierra. Cada cambio
se aplica y se guarda al momento.

Los valores viven en `~/.config/cc-dash/config.toml`. Un dashboard abierto
los relee en un par de segundos, sin reiniciarlo. `X` apaga o enciende los
toasts de forma permanente; las novedades siguen llegando al registro (`n`).

## Ver el documento que hay que aprobar

Cuando una sesión escribe un spec o un plan y se detiene a preguntarte, hay
dos formas de leerlo sin salir de la terminal:

- **`D`** en el dashboard abre el último `.md` que escribió o editó la sesión
  enfocada. Si la sesión te espera, el panel del lead lo recuerda
  (`◆ necesita una respuesta · D abre login-design.md`).
- **Un hook de Claude Code** lo abre solo, encima del panel donde corre la
  sesión, en el momento en que pregunta:

  ```sh
  cc-dash hook install     # lo añade al settings.json de cada cuenta
  cc-dash hook status
  cc-dash hook remove
  cc-dash -a trabajo hook install   # solo una cuenta
  ```

  El hook se dispara en dos momentos y mira solo los `.md` escritos desde tu
  último mensaje:
  - al hacerte una pregunta con opciones (`AskUserQuestion`): abre el último;
  - al terminar el turno: abre el último cuya ruta cumpla `spec_match`
    (por defecto `spec|plan|design|diseño|prd|rfc|propuesta|proposal`), para
    no saltar con cualquier README.

  No repite un archivo que ya mostró salvo que haya cambiado. Si en ese
  momento no estás mirando el panel de la sesión, o no hay tmux, no abre nada
  y deja la ruta en un mensaje de Claude Code. Las sesiones ya abiertas toman
  el hook al reiniciarse.

El programa que abre el documento es configurable:

```sh
cc-dash set viewer "glow -p"              # la ruta se añade al final
cc-dash set viewer "nvim -R {file}"       # o donde pongas {file}
cc-dash set viewer ""                     # automático: glow, bat o less
cc-dash set viewer_popup false            # sin popup: para visores con ventana propia
cc-dash set viewer "code {file}"          #   …como un editor gráfico
cc-dash view docs/spec.md                 # prueba el visor con un archivo
```

Con `viewer_popup = true` (por defecto) se abre en un popup de tmux al 90 %
de la pantalla, que se cierra al salir del visor; por eso conviene un visor
que se quede abierto (`glow -p` y no `glow`). Fuera de tmux, `D` y
`cc-dash view` le ceden la terminal al visor hasta que lo cierres. El visor
también se elige en la ventana de configuración (`,`).

## Sesiones que esperan tu respuesta

Cuando una sesión se detiene a la espera de un permiso, una pregunta, la
aprobación de un plan o un diálogo, se marca en amarillo con `◆`: en la
cabecera (`◆ 1 ESPERA TU RESPUESTA`), en la lista de sesiones y en el panel
del lead, con lo que necesita (`necesita permiso para Bash: …`). En modo
automático el dashboard enfoca primero la sesión que espera.

## Otras máquinas

Un solo dashboard puede mostrar las sesiones de varias PCs. En cada máquina
remota hace falta tener cc-dash instalado y poder entrar por ssh sin
contraseña (con clave). En la máquina del dashboard:

```sh
cc-dash remote add pc-oficina     # el nombre de ssh, como en ~/.ssh/config
cc-dash remote add laptop
cc-dash remote                    # lista las máquinas
cc-dash remote remove laptop
```

Al arrancar, el dashboard ejecuta `ssh MÁQUINA cc-dash agent` por cada una.
El agente lee las cuentas de esa máquina, con su propia configuración, y
manda por ssh lo que va pasando. No abre puertos ni necesita contraseñas
aparte de la clave de ssh.

- Las sesiones remotas se ven como las locales, con la cuenta como
  `cuenta@máquina` (por ejemplo `default@laptop`); `c` también filtra por ellas.
- La leyenda de la cabecera muestra cada máquina: `●` conectada, `◌`
  conectando, `✗` sin conexión. Si una se cae, sus sesiones salen como
  cerradas y el dashboard reintenta solo cada 10 segundos.
- Lo que depende de archivos o de tmux de esta máquina no funciona con una
  sesión remota: saltar a su panel (`Enter`), abrir su `.md` (`D`), la vista de
  cambios (`e`) y el estado de git.
- Las dos puntas deben tener versiones compatibles de cc-dash; si no, la
  máquina sale como sin conexión y lo indica. `auto_update` ayuda a
  mantenerlas al día.

## Cuentas

Una cuenta es un directorio de configuración de Claude Code, es decir, el
valor de `CLAUDE_CONFIG_DIR` con el que se lanza `claude` para esa cuenta.
Sin configurar nada se muestra `~/.claude` como cuenta `default`.

```sh
cc-dash add trabajo ~/.claude-trabajo   # registra una cuenta
cc-dash scan /srv/dev/claude            # cada subcarpeta es una cuenta
cc-dash accounts                        # lista cuentas, email y sesiones
cc-dash remove trabajo
cc-dash config                          # ruta del archivo de configuración
```

Una cuenta se puede deshabilitar sin quitarla: deja de leerse y sus sesiones
desaparecen del dashboard hasta que la vuelvas a activar.

```sh
cc-dash disable trabajo
cc-dash enable trabajo
```

Desde el dashboard, la ventana de configuración (`,`) tiene al final una
sección **Cuentas** con un interruptor por cada una; el cambio se aplica al
momento. `cc-dash accounts` muestra el estado de cada cuenta.

El archivo es `~/.config/cc-dash/config.toml` y se puede editar a mano:

```toml
include_default = true          # añade ~/.claude si no está listada
auto_update = true              # se actualiza solo al arrancar
layout = "lista"                # diseño del panel de agentes
theme = "neon"                  # clasico, neon, matrix, ambar, catppuccin-*, terminal
alerts = true                   # pitido y aviso de tmux
effects = true                  # arranque, glitch y lluvia en reposo
scan = ["/srv/dev/claude"]
remotes = ["pc-oficina"]        # otras máquinas, por ssh

[[account]]
name = "trabajo"
path = "/home/yo/.claude-trabajo"
```

## Desarrollo

El código está en el paquete `cc_dash/`, un módulo por responsabilidad:

| módulo | qué hace |
|---|---|
| `sources` | de dónde salen los datos: `LocalSource` (este disco), `RemoteSource` (otra máquina por ssh) y `cc-dash agent` |
| `model` | lo que se entiende de los transcripts (un método por tipo de registro): sesiones, subagentes, estado, atascos |
| `config` | cuentas, `config.toml` y los umbrales vigentes (`limits`) |
| `theme` | temas y la paleta activa `C` (`C.LEAD`, `C.DIM`…) |
| `text` | utilidades de texto puras |
| `repo` | consultas a git |
| `game` | HP, rondas, niveles y evoluciones |
| `canvas`, `faces` | lienzo, gráfico de actividad y rostros de braille |
| `agents_view` | el panel de agentes en sus cinco diseños |
| `log_view` | el session log y el detalle de cada entrada |
| `panels`, `settings` | el resto de paneles y la ventana de configuración |
| `external`, `hooks` | tmux, visores, vista de cambios y el hook de Claude Code |
| `screen` | compone la pantalla completa y decide qué avisar |
| `app` | el dashboard en marcha: refresco y una cadena de manejadores de teclas |
| `cli` | subcomandos y arranque |

`./check.sh` revisa la salud del código. La primera vez crea `.venv` con las
herramientas de `requirements-dev.txt`.

- **tests** (`tests/`, con pytest): lo que el dashboard entiende de los
  transcripts, la configuración, la línea de órdenes, git y los hooks, que
  cada diseño, tema y ventana se dibuja, y una pasada por todas las teclas en
  una pseudo-terminal. `tests/builder.py` genera cuentas de prueba.
- **ruff:** errores y malas prácticas; la configuración está en
  `pyproject.toml`.
- **vulture:** código muerto.
- **`tools/quality.py`:** complejidad por función (radon), tamaño, funciones
  largas y nombres locales que tapan uno de fuera. Compara con
  `quality-baseline.json` (también la cobertura de los tests) y falla si algo
  empeora. Cuando una métrica mejora,
  `./check.sh --update` la fija como nueva línea base.
