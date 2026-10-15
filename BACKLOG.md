# Backlog

## Enviar comandos a las sesiones desde el dashboard

Escribir en el panel de tmux de la sesión enfocada con `tmux send-keys` (el
mismo panel que ya localiza `Enter` para saltar a la sesión).

- **Enviar un prompt:** una tecla (p. ej. `i`) abre una línea de texto; Enter
  la manda a la sesión enfocada. Sirve también para `/compact`, `/clear`, etc.
- **Responder permisos:** con la sesión en `◆ necesita permiso…`, teclas
  rápidas para sí / sí siempre / no, sin cambiar de panel.
- **Interrumpir:** mandar `Esc` a una sesión que trabaja o parece atascada,
  con confirmación.

Límites y cuidados:

- Solo sesiones dentro de tmux; fuera de tmux no hay forma de escribirles
  (`claude -p --resume` abre otra ejecución, no la que está abierta).
- Las teclas llegan a lo que haya en pantalla: bloquear el envío según el
  estado (no mandar un prompt si hay un permiso pendiente o la sesión trabaja)
  y mostrar una vista previa antes de enviar.
- El orden de las opciones del diálogo de permisos puede cambiar entre
  versiones de Claude Code: verificarlo antes de mapear teclas.

Por dónde empezar: enviar prompts y responder permisos.
