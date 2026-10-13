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
- `c`: filtra por cuenta · `i`: actividad (en terminales estrechas) · `q`: sale

El panel a la derecha del log muestra el subagente seleccionado (respuesta o
prompt) o, si no hay ninguno, la actividad de la sesión: porcentaje de la
ventana de contexto, tokens y caché, turnos, herramientas más usadas, errores,
skills, archivos editados y leídos, servidores MCP, subagentes por tipo, hooks
y líneas de código cambiadas.

La ventana de contexto no figura en los transcripts: se asume 1M si el modelo
lo indica (`[1m]`) o si el contexto ya pasa de 200k, y 200k en otro caso.

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
scan = ["/srv/dev/claude"]

[[account]]
name = "trabajo"
path = "/home/yo/.claude-trabajo"
```
