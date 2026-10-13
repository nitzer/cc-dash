# cc-dash

Dashboard en vivo, en la terminal, de las sesiones de Claude Code: qué sesiones
están abiertas, qué modelo lleva cada una, herramientas en curso, subagentes,
tokens y un log cronológico. Soporta varias cuentas en la misma máquina.

Solo lee archivos locales de Claude Code; no modifica ni envía nada.

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

Teclas:

- `↑`/`↓` o `j`/`k`: cambia de sesión · `a`: vuelve al modo automático
- `←`/`→`: selecciona un subagente · `Esc`: lo suelta
- `r`: última respuesta del subagente · `p`: su prompt
- `t`: muestra u oculta los subagentes ya terminados (se retiran solos del
  diagrama un minuto después de acabar)
- `AvPág`/`RePág` (o espacio/`b`): desplaza el detalle
- `f`: limita el log a la sesión enfocada; con un subagente seleccionado el
  log muestra solo lo de ese subagente
- `s`: pone el cursor en el session log. El panel de la derecha pasa a ser un
  recuadro de color con el detalle completo de la entrada elegida: los
  parámetros de la herramienta y su salida, el prompt entero, o el prompt y
  la respuesta de un subagente. Se navega al estilo vim:
  - `j`/`k`: recorre las entradas del log · `g` primera · `G` última (y sigue
    a las nuevas)
  - `l` (o `→`, `Enter`): entra al detalle; ahí `j`/`k` lo desplazan
  - `h` (o `←`): vuelve al log
  - `AvPág`/`RePág` desplazan el detalle siempre · `s` o `Esc` cierra
- `v`: muestra la conversación de la sesión enfocada en lugar del diagrama,
  con las mismas teclas (`j`/`k` cambia de sesión, `l` entra, `h` vuelve) y
  una tarjeta con los datos de la sesión bajo la lista
- `d`: cambia el diseño del panel de agentes y lo guarda en la configuración:
  - `lista`: dos líneas por subagente y una barra de tiempo a la derecha
  - `tabla`: una línea por subagente, en columnas
  - `tarjetas`: tarjetas en cuadrícula
  - `tiempo`: línea de tiempo con una barra para el lead y otra por subagente
  - `diagrama`: el diagrama original con flechas

  También se puede elegir con `cc-dash --layout tabla`
- `c`: filtra por cuenta · `i`: actividad (en terminales estrechas) · `q`: sale

El panel a la derecha del log muestra el subagente seleccionado (respuesta o
prompt) o, si no hay ninguno, la actividad de la sesión: porcentaje de la
ventana de contexto, tokens y caché, turnos, herramientas más usadas, errores,
skills, archivos editados y leídos, servidores MCP, subagentes por tipo, hooks
y líneas de código cambiadas.

La ventana de contexto no figura en los transcripts: se asume 1M si el modelo
lo indica (`[1m]`) o si el contexto ya pasa de 200k, y 200k en otro caso.

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

El archivo es `~/.config/cc-dash/config.toml` y se puede editar a mano:

```toml
include_default = true          # añade ~/.claude si no está listada
auto_update = true              # se actualiza solo al arrancar
layout = "lista"                # diseño del panel de agentes
scan = ["/srv/dev/claude"]

[[account]]
name = "trabajo"
path = "/home/yo/.claude-trabajo"
```
