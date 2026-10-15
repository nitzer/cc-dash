"""Lo que el dashboard entiende de los transcripts: estado, herramientas, subagentes, atascos."""
import time

import cc_dash as c
from builder import SID, SideTranscript, Transcript


def session(store):
    return store.sessions[SID]


def test_basic_session(acct, load):
    tr = (Transcript().prompt("arregla el login")
          .meta("ai-title", aiTitle="Arreglar login")
          .meta("permission-mode", permissionMode="plan")
          .tool("t1", "Bash", out=20, ctx=5000, command="pytest", description="corre los tests")
          .result("t1", "3 passed")
          .text("listo", out=5, ctx=6000)
          .turn_end(4000))
    acct.write(tr)
    s = session(load())
    assert s.title == "Arreglar login"
    assert s.perm == "plan"
    assert s.cwd == "/tmp/proyecto"
    assert s.model == "claude-opus-5-5"
    assert s.tools == 1 and s.tool_counts["Bash"] == 1
    assert s.out_tokens == 25
    assert s.ctx == 6000
    assert s.turns == 1 and s.turn_last == 4000
    assert [i["k"] for i in s.items] == ["prompt", "tool", "text"]
    assert s.items[1]["label"] == "corre los tests"
    assert s.items[1]["res"] == ["3 passed"]


def test_output_tokens_counted_once_per_request(acct, load):
    tr = Transcript().prompt("hola")
    tr.text("a", out=10, rid="r1").text("b", out=25, rid="r1").text("c", out=5, rid="r2")
    acct.write(tr)
    assert session(load()).out_tokens == 30   # r1 crece de 10 a 25: cuenta 25, más 5


def test_reads_new_lines_incrementally(acct, load):
    tr = Transcript().prompt("uno")
    path = acct.write(tr)
    store = load()
    assert session(store).tools == 0
    more = Transcript(start=tr.ts).tool("t1", "Read", file_path="/tmp/proyecto/a.py")
    acct.write(more, append=True)
    with open(path, "a") as fh:
        fh.write('{"type": "assistant", "sessionId": "x"')   # línea a medio escribir: se espera
    load()
    s = session(store)
    assert s.tools == 1
    assert "/tmp/proyecto/a.py" in s.reads


def test_state_from_live_sessions(acct, load):
    acct.write(Transcript().prompt("hola"))
    assert load().state(session(load())) == "ended"
    acct.open_session(status="busy")
    store = load()
    assert store.state(session(store)) == "busy"
    acct.open_session(status="idle")
    assert load().state(session(store)) == "idle"


def test_dead_process_means_ended(acct, load):
    acct.write(Transcript().prompt("hola"))
    acct.open_session(status="busy", pid=2 ** 22 + 12345)
    store = load()
    assert store.state(session(store)) == "ended"


def test_waiting_for_permission(acct, load):
    acct.write(Transcript().prompt("borra build").tool("t1", "Bash", command="rm -rf build"))
    acct.open_session(status="waiting", waitingFor="permission prompt")
    store = load()
    s = session(store)
    assert store.state(s) == "waiting"
    assert store.waiting_for(s) == "permiso para Bash: rm -rf build"


def test_pending_question_counts_as_waiting(acct, load):
    acct.write(Transcript().prompt("elige").tool("t1", "AskUserQuestion", questions=[]))
    acct.open_session(status="busy")
    store = load()
    assert store.state(session(store)) == "waiting"
    assert store.waiting_for(session(store)) == "responder una pregunta"


def test_background_subagent(acct, load):
    tr = (Transcript().prompt("revisa")
          .tool("ag1", "Agent", subagent_type="Explore", description="buscar usos", prompt="...")
          .result("ag1", "lanzado", agentId="aaa1", isAsync=True))
    acct.write(tr)
    side = SideTranscript("aaa1", start=tr.ts)
    side.tool("s1", "Grep", pattern="login").result("s1").tool("s2", "Read", file_path="/tmp/proyecto/x.py")
    acct.write_side(SID, side, "Explore", "ag1")
    acct.open_session(status="busy")
    store = load()
    s = session(store)
    (v,) = store.agents(s)
    assert v["type"] == "Explore" and v["desc"] == "buscar usos"
    assert v["aid"] == "aaa1"
    assert v["running"]
    assert v["tools"] == 2
    assert v["rounds"] == 2
    assert v["tool"] == ("Read", "x.py")

    store.ready = True
    done = Transcript(start=side.ts).meta(
        "queue-operation", operation="enqueue",
        content="<task-notification><task-id>aaa1</task-id><status>completed</status></task-notification>")
    acct.write(done, append=True)
    load()
    (v,) = store.agents(s)
    assert not v["running"] and v["status"] == "completed"
    assert [a[1] for a in store.alerts] == ["agent"]


def test_foreground_subagent_result(acct, load):
    tr = (Transcript().prompt("planifica")
          .tool("ag1", "Task", subagent_type="Plan", description="plan")
          .result("ag1", "el plan es…", agentId="bbb2"))
    acct.write(tr)
    store = load()
    (v,) = store.agents(session(store))
    assert v["status"] == "completed"
    assert v["response"] == "el plan es…"


def test_stuck_on_errors_and_repeats(acct, load):
    tr = Transcript().prompt("dale")
    for n in range(3):
        tr.tool(f"e{n}", "Bash", command="make").result(f"e{n}", "fallo", error=True)
    acct.write(tr)
    acct.open_session(status="busy")
    store = load()
    why = store.stuck(session(store), time.time())
    assert any("3 errores seguidos en Bash" in w for w in why)
    assert any("repite Bash 3 veces" in w for w in why) is False   # el umbral de repetición es 4


def test_stuck_tool_without_answer(acct, load):
    tr = Transcript(start=time.time() - 1000).prompt("dale").tool("t1", "Bash", command="sleep 999")
    acct.write(tr)
    acct.open_session(status="busy")
    store = load()
    why = store.stuck(session(store), time.time())
    assert any(w.startswith("Bash lleva") for w in why)


def test_limits_come_from_config(acct, load):
    tr = Transcript(start=time.time() - 10).prompt("dale").tool("e1", "Bash", command="x").result("e1", "no", error=True)
    acct.write(tr)
    acct.open_session(status="busy")
    store = load()
    assert store.stuck(session(store), time.time()) == []
    c.apply_limits({**c.load_config(), "stuck_errors": 1})
    assert store.stuck(session(store), time.time())


def test_todo_list_progress(acct, load):
    todos = [{"content": "a", "activeForm": "haciendo a", "status": "completed"},
             {"content": "b", "activeForm": "haciendo b", "status": "in_progress"},
             {"content": "c", "activeForm": "", "status": "pending"}]
    acct.write(Transcript().prompt("tareas").tool("t1", "TodoWrite", todos=todos))
    assert session(load()).progress() == (1, 3, "haciendo b")


def test_task_create_and_update(acct, load):
    tr = (Transcript().prompt("tareas")
          .tool("t1", "TaskCreate", subject="migrar db").result("t1", "Task #7 created")
          .tool("t2", "TaskUpdate", taskId="7", status="in_progress"))
    acct.write(tr)
    s = session(load())
    assert s.todos["7"]["subject"] == "migrar db"
    assert s.progress() == (0, 1, "migrar db")


def test_background_job_finishes_with_alert(acct, load):
    tr = (Transcript().prompt("compila")
          .tool("t1", "Bash", command="make", description="compilar")
          .result("t1", "en segundo plano", backgroundTaskId="job9"))
    acct.write(tr)
    store = load()
    store.ready = True
    acct.write(Transcript(start=tr.ts).meta(
        "queue-operation", operation="enqueue",
        content="<task-notification><task-id>job9</task-id><status>failed</status></task-notification>"),
        append=True)
    load()
    job = session(store).jobs["job9"]
    assert job["status"] == "failed"
    assert store.alerts[-1][1] == "fail"


def test_written_markdown_is_a_doc(acct, load):
    acct.write(Transcript().prompt("spec").tool("t1", "Write", file_path="docs/spec.md", content="x"))
    assert session(load()).docs == ["/tmp/proyecto/docs/spec.md"]


def test_log_keeps_roles_not_colors(acct, load):
    acct.write(Transcript().prompt("hola").tool("t1", "Bash", command="ls"))
    styles = [e[2] for e in load().log]
    assert styles == ["LEAD", "TOOL"]


def test_game_levels():
    g = c.game(125, 50_000, 200_000)
    assert g == {"hp": 75, "lvl": 13, "xp": 5, "rounds": 125, "tier": 1}
    assert c.game(0, 0, 200_000)["lvl"] == 1
    assert c.game(10_000, 0, 200_000)["tier"] == 4
