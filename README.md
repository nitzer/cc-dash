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

Las principales:

- `↑`/`↓` o `j`/`k`: cambia de sesión · `a`: vuelve al modo automático
- `←`/`→`: selecciona un subagente · `r`/`p`: su respuesta o su prompt
- `s`: cursor en el session log, con el detalle de cada entrada a la derecha
- `o`: tareas · `n`: novedades · `v`: conversación de la sesión
- `D`: abre el último `.md` que escribió la sesión (un spec, un plan…)
- `d`: diseño · `T`: tema · `,`: configuración · `q`: sale

`d` alterna el panel de agentes entre cinco diseños: `lista`, `tabla`,
`tarjetas`, `tiempo` y `diagrama`. En `diagrama` cada subagente es una tarjeta
con un rostro cyberpunk que cuelga del lead: ronin, calavera, androide, oni,
netrunner o cíclope, según el tipo de subagente. Son máscaras angulares hechas
de placas, con puntos de braille en relleno plano o tramado, en el color del
subagente con brillos cian, y los ojos encendidos en un color que contrasta. Las tarjetas aparecen a medida que
el lead crea subagentes, sin casillas vacías: en una fila con rostros grandes
o, si no caben, en dos filas con rostros más pequeños. Mientras trabaja, los
ojos laten (el visor barre de lado a lado) y la imagen se corrompe a ratos
cuando usa una herramienta. Al terminar, el rostro se apaga; si falla, se
agrieta y se tiñe de rojo.

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

## Saltar a una sesión, avisos, pulso y temas

- **`Enter`** lleva tu cliente de tmux al panel donde corre la sesión
  enfocada. Hace falta que tanto `cc-dash` como la sesión estén dentro de tmux
  en la misma máquina.
- **Avisos:** cuando una sesión pasa a esperarte, cuando termina tras más de
  un minuto de trabajo o cuando falla un subagente, suena el pitido de la
  terminal, aparece un mensaje en la barra de tmux y queda una línea `aviso`
  en el log. `m` los silencia (`alerts = false` en la configuración).
- **Actividad:** al pie del panel de agentes, en todos los diseños, un
  gráfico de barras con las herramientas que usó la sesión enfocada en los
  últimos 10 minutos. Las barras se apilan por quién las usó: el lead en
  naranja y cada subagente en su color. Cuanto más uso, más intenso el color.
  La barra de ahora destella con cada herramienta nueva, y el rombo de la
  leyenda late más rápido cuanto más ritmo hay. La leyenda muestra cuántas
  herramientas aportó cada uno, más las herramientas y los tokens por minuto.
- **Temas:** `T` cambia entre `clasico`, `neon`, `matrix` y `ambar`; los tres
  últimos usan un marco tipo HUD (solo esquinas). También `cc-dash --theme neon`.

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
theme = "neon"                  # clasico, neon, matrix o ambar
alerts = true                   # pitido y aviso de tmux
effects = true                  # arranque, glitch y lluvia en reposo
scan = ["/srv/dev/claude"]

[[account]]
name = "trabajo"
path = "/home/yo/.claude-trabajo"
```
