"""Gamificación: HP, rondas, niveles y evoluciones de cada agente."""


def game(rounds, ctx, window):
    """HP = contexto libre; cada 10 rondas sube un nivel; cada 10 niveles el rostro evoluciona."""
    used = min(100, round(100 * ctx / window)) if ctx and window else 0
    lvl = rounds // 10 + 1
    return {"hp": 100 - used, "lvl": lvl, "xp": rounds % 10, "rounds": rounds, "tier": min(4, lvl // 10)}


def lead_game(s):
    return game(s.turns, s.ctx, s.window())


def agent_game(v):
    return game(v["rounds"], v["ctx"], 1_000_000 if v["ctx"] > 200_000 else 200_000)


EVO = ("", "EVO I", "EVO II", "EVO III", "EVO MAX")
EVO_GAIN = ("el rostro de partida", "una cresta en punta sobre la frente",
            "implantes en las mejillas que brillan con los ojos", "un halo de puntos sobre la cabeza",
            "brillos dorados en lugar de cian")
