"""Test offline: server MCP -> HTTP -> td_bridge.py -> finto TouchDesigner.

    python test_offline.py

Prova il protocollo, il token e ogni tool contro fake_td.py. Non prova le API
vere di TouchDesigner: per quello serve check_live.py con TouchDesigner aperto.
"""
import asyncio
import importlib
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fake_td  # noqa: E402

fake_td.install()
home = tempfile.mkdtemp()
os.environ["HOME"] = os.environ["USERPROFILE"] = home
os.environ.pop("AJ_TD_TOKEN", None)

# l'installer gira "dentro TouchDesigner": crea token, COMP, callbacks e server
exec((HERE / "install_bridge.py").read_text(encoding="utf-8"), {"BRIDGE_DIR": str(HERE)})
comp = fake_td.op("/project1/aj_mcp")
assert comp is not None and comp.op("server").par.port.val == 9990
assert "onHTTPRequest" in comp.op("callbacks").text
token_file = Path(home) / ".aj-mcp" / "touchdesigner.token"
token = token_file.read_text(encoding="utf-8")
assert len(token) > 30

# rilanciarlo non duplica i nodi e non cambia il token
exec((HERE / "install_bridge.py").read_text(encoding="utf-8"), {"BRIDGE_DIR": str(HERE)})
assert len(fake_td.op("/project1").children) == 1
assert token_file.read_text(encoding="utf-8") == token

bridge = importlib.import_module("td_bridge")
server = fake_td.serve(bridge)
os.environ["AJ_TD_URL"] = f"http://127.0.0.1:{server.server_address[1]}"

m = importlib.import_module("touchdesigner_mcp")

# --- solo richieste locali, in tutte le forme in cui TouchDesigner passa l'indirizzo
for addr, ok in (("127.0.0.1", True), ("127.0.0.1:54321", True), ("::1", True), ("[::1]:54321", True),
                 ("::ffff:127.0.0.1", True), ("localhost", True), ("192.168.1.20", False),
                 ("192.168.1.20:80", False), ("::ffff:10.0.0.5", False), ("", False), (None, False)):
    assert bridge._is_local(addr) is ok, addr

# --- token
os.environ["AJ_TD_TOKEN"] = "sbagliato"
try:
    m.td_status()
    raise AssertionError("un token errato doveva essere rifiutato")
except RuntimeError as e:
    assert "401" in str(e), e
os.environ.pop("AJ_TD_TOKEN")

# --- lettura
s = m.td_status()
assert s["project"] == "finto.toe" and s["touchdesigner"] == "2026.10000", s
assert "noiseTOP" in m.list_operator_types("TOP")["types"]
assert "baseCOMP" in m.list_operator_types()["types"]

# --- crea, collega, imposta
a = m.create_node("noiseTOP", name="rumore", x=0, y=0)
b = m.create_node("levelTOP")
assert a["path"] == "/project1/rumore" and b["name"] == "level1", (a, b)
m.connect_nodes(a["path"], b["path"])
info = m.node_info(b["path"])
assert info["inputs"] == ["/project1/rumore"] and info["resolution"] == [1280, 720], info
assert m.node_info(a["path"])["outputs"] == ["/project1/level1"]

r = m.set_parameters(b["path"], {"contrast": 1.5})
assert r["values"] == {"contrast": 1.5}, r
pars = m.get_parameters(b["path"], "contr")
assert [p["name"] for p in pars["parameters"]] == ["contrast"] and pars["parameters"][0]["value"] == 1.5
m.pulse_parameter(a["path"], "period")
assert fake_td.op(a["path"]).par.period.pulsed == 1

listed = m.list_nodes("/project1")
assert {n["name"] for n in listed["nodes"]} == {"aj_mcp", "rumore", "level1"}, listed
assert m.list_nodes("/project1", depth=2)["count"] == 5
assert m.node_errors()["errors"] == ""

# --- anteprima e Python
img = m.top_preview(a["path"])
assert img.data[:8] == b"\x89PNG\r\n\x1a\n"
out = m.run_python("print('ciao'); result = len(op('/project1').children)")
assert out == {"result": 3, "stdout": "ciao\n"}, out

# --- errori leggibili
for bad, needle in (
    (lambda: m.node_info("/project1/manca"), "nodo non trovato"),
    (lambda: m.create_node("inventatoTOP"), "tipo di operatore sconosciuto"),
    (lambda: m.set_parameters(a["path"], {"nonesiste": 1}), "non ha il parametro"),
    (lambda: m.delete_node("/project1"), "nodo di sistema"),
    (lambda: m.top_preview("/project1/aj_mcp/callbacks"), "non e' un TOP"),
    (lambda: m.run_python("1/0"), "ZeroDivisionError"),
):
    try:
        bad()
        raise AssertionError(f"mancava l'errore: {needle}")
    except RuntimeError as e:
        assert needle in str(e), (needle, e)

# --- 1.1: espressioni e bind, validazione prima di cambiare
r = m.set_parameters(b["path"], {"contrast": {"expr": "absTime.seconds"}, "brightness1": 2.0})
assert r["values"] == {"contrast": "<absTime.seconds>", "brightness1": 2.0}, r
assert fake_td.op(b["path"]).par.contrast.mode == fake_td.ParMode.EXPRESSION
m.set_parameters(b["path"], {"contrast": 1.0})
assert fake_td.op(b["path"]).par.contrast.mode == fake_td.ParMode.CONSTANT
try:
    m.set_parameters(b["path"], {"brightness1": 9, "manca": 1})
except RuntimeError:
    pass
assert fake_td.op(b["path"]).par.brightness1.val == 2.0, "con un nome sbagliato non doveva cambiare niente"

# --- ogni comando che scrive e' un passo di annullamento
undo = fake_td.ui.undo
assert undo.open == 0 and "AJ MCP set_parameters" in undo.blocks
before = len(undo.blocks)
m.node_info(b["path"])
assert len(undo.blocks) == before, "le letture non devono aprire passi di annullamento"
assert m.undo(steps=2) == {"action": "undo", "steps": 2} and undo.undone == 2
assert m.undo(redo=True)["action"] == "redo" and undo.redone == 1

# --- ricerca
assert [n["name"] for n in m.find_nodes(name="rum*")["nodes"]] == ["rumore"]
assert m.find_nodes(family="DAT")["count"] == 2
assert [n["name"] for n in m.find_nodes(op_type="levelTOP")["nodes"]] == ["level1"]

# --- build_node: crea, imposta, collega, controlla; se fallisce non lascia niente
c = m.build_node("levelTOP", name="finale", params={"contrast": 2.5}, inputs=[b["path"]])
assert c["inputs"] == [b["path"]] and c["params"] == {"contrast": 2.5} and c["errors"] == "", c
for bad in ({"params": {"inventato": 1}}, {"inputs": ["/project1/manca"]}):
    try:
        m.build_node("levelTOP", name="rotto", **bad)
        raise AssertionError("build_node doveva fallire")
    except RuntimeError:
        pass
    assert fake_td.op("/project1/rotto") is None, "build_node ha lasciato un nodo a meta'"

# --- rinomina, copia, stacca, stato, layout
assert m.rename_node(c["path"], "uscita") == {"old": "/project1/finale", "path": "/project1/uscita"}
for bad, needle in ((lambda: m.rename_node("/project1/uscita", "rumore"), "esiste gia'"),
                    (lambda: m.rename_node("/project1", "x"), "nodo di sistema")):
    try:
        bad()
        raise AssertionError(needle)
    except RuntimeError as e:
        assert needle in str(e), e
cp = m.copy_node(a["path"], name="rumore2")
assert cp["path"] == "/project1/rumore2" and cp["x"] == 200, cp
assert fake_td.op(cp["path"]).par.period.val == 1.0
m.disconnect_nodes("/project1/uscita")
assert m.node_info("/project1/uscita")["inputs"] == [] and m.node_info(b["path"])["outputs"] == []
st = m.set_node_state(a["path"], color=[1, 0, 0], comment="sorgente", bypass=True, x=-50)
assert st["color"] == [1.0, 0.0, 0.0] and st["bypass"] is True and st["x"] == -50, st
try:
    m.set_node_state(a["path"], display=True)
    raise AssertionError("un TOP non ha il flag display")
except RuntimeError as e:
    assert "non ha il flag" in str(e), e
m.connect_nodes(b["path"], "/project1/uscita")
lay = m.auto_layout()
assert lay["columns"] == 3, lay
assert (fake_td.op(a["path"]).nodeX, fake_td.op(b["path"]).nodeX, fake_td.op("/project1/uscita").nodeX) == (0, 200, 400)

# --- DAT e CHOP
t = m.create_node("textDAT", name="note")
m.write_dat(t["path"], text="velocita = 1\ncolore = rosso")
assert m.read_dat(t["path"])["rows_total"] == 2
assert m.edit_dat(t["path"], "rosso", "blu") == {"path": "/project1/note", "replaced": 1}
assert "blu" in fake_td.op(t["path"]).text
tab = m.create_node("tableDAT", name="tabella")
m.write_dat(tab["path"], rows=[["nome", "valore"], ["a", 1]])
assert m.read_dat(tab["path"])["rows"] == [["nome", "valore"], ["a", "1"]]
for bad, needle in ((lambda: m.write_dat(t["path"], text=""), "confirm_empty"),
                    (lambda: m.write_dat(t["path"]), "text oppure rows"),
                    (lambda: m.edit_dat(t["path"], "="), "compare 2 volte"),
                    (lambda: m.edit_dat(t["path"], "assente"), "non trovato"),
                    (lambda: m.read_chop(t["path"]), "non e' un CHOP")):
    try:
        bad()
        raise AssertionError(needle)
    except RuntimeError as e:
        assert needle in str(e), (needle, e)
assert m.edit_dat(t["path"], "=", ":", replace_all=True)["replaced"] == 2
k = m.create_node("constantCHOP", name="valori")
assert m.read_chop(k["path"], max_samples=2)["channels"] == {"chan1": [0.0, 0.25]}

# --- metodi, aiuto, prestazioni, timeline
assert m.exec_node_method(a["path"], "cook", kwargs={"force": True})["result"] is None
try:
    m.exec_node_method(a["path"], "__class__")
    raise AssertionError("i nomi con _ vanno rifiutati")
except RuntimeError as e:
    assert "non consentito" in str(e), e
h = m.python_help("noiseTOP", filter="cook")
assert any(x["name"] == "cook" for x in h["members"]), h
assert m.python_help(a["path"])["name"] == a["path"]
perf = m.get_performance(top=3)
assert len(perf["slowest"]) == 3 and perf["fps_target"] == 60.0, perf
assert m.timeline(play=False, frame=120)["play"] is False and fake_td.ROOT.time.frame == 120.0

# --- tox e progetto
work = Path(tempfile.mkdtemp())
box = m.create_node("baseCOMP", name="scatola")
assert m.save_tox(box["path"], str(work / "scatola.tox"))["file"].endswith("scatola.tox")
assert m.load_tox(str(work / "scatola.tox"), name="caricata")["path"] == "/project1/caricata"
for bad, needle in ((lambda: m.save_tox(a["path"], str(work / "x.tox")), "solo i COMP"),
                    (lambda: m.save_tox(box["path"], str(work / "x.txt")), ".tox"),
                    (lambda: m.load_tox(str(work / "manca.tox")), "file non trovato")):
    try:
        bad()
        raise AssertionError(needle)
    except RuntimeError as e:
        assert needle in str(e), (needle, e)
assert m.save_project()["saved"] is True and fake_td.project.saved == "(stesso file)"

# --- batch: un solo passo di annullamento, si ferma al primo errore
before = len(undo.blocks)
res = m.batch([{"cmd": "create_node", "args": {"op_type": "noiseTOP", "name": "lotto"}},
               {"cmd": "set_parameters", "args": {"path": "/project1/lotto", "values": {"period": 3}}}])
assert res["steps"] == 2 and fake_td.op("/project1/lotto").par.period.val == 3, res
assert undo.blocks[before:] == ["AJ MCP batch"], undo.blocks[before:]
try:
    m.batch([{"cmd": "delete_node", "args": {"path": "/project1/lotto"}},
             {"cmd": "node_info", "args": {"path": "/project1/lotto"}}])
    raise AssertionError("batch doveva fallire")
except RuntimeError as e:
    assert "passo 1" in str(e) and "dopo 1 riusciti" in str(e), e
for x in ("/project1/note", "/project1/tabella", "/project1/valori", "/project1/scatola",
          "/project1/caricata", "/project1/rumore2", "/project1/uscita"):
    m.delete_node(x)

# --- elimina
assert m.delete_node(b["path"]) == {"deleted": "/project1/level1"}
assert fake_td.op("/project1/level1") is None

# --- avvio: con il ponte attivo non lancia niente
os.environ["AJ_TD_EXE"] = str(HERE / "non_esiste.exe")
already = m.open_touchdesigner()
assert already["opened"] is False and "gia' aperto" in already["note"], already

# --- TouchDesigner spento
server.shutdown()
server.server_close()
try:
    m.td_status()
    raise AssertionError("mancava l'errore di collegamento")
except RuntimeError as e:
    assert "non risponde" in str(e), e

# --- il server MCP registra i tool
# --- avvio a programma chiuso: eseguibile mancante, poi eseguibile finto che non apre il ponte
try:
    m.open_touchdesigner(wait_seconds=1)
    raise AssertionError("mancava l'errore di eseguibile non trovato")
except RuntimeError as e:
    assert "non trovato" in str(e), e
os.environ["AJ_TD_EXE"] = sys.executable
finto = Path(tempfile.mkdtemp()) / "finto.py"
finto.write_text("pass\n", encoding="utf-8")
try:
    m.open_touchdesigner(str(finto), wait_seconds=1)
    raise AssertionError("mancava l'errore di ponte che non risponde")
except RuntimeError as e:
    assert "avviato" in str(e) and "ponte non risponde" in str(e), e
try:
    m.open_touchdesigner(str(finto) + ".manca", wait_seconds=1)
    raise AssertionError("mancava l'errore di file non trovato")
except RuntimeError as e:
    assert "file non trovato" in str(e), e
os.environ.pop("AJ_TD_EXE")

tools = asyncio.run(m.mcp.list_tools())
names = sorted(t.name for t in tools)
assert len(names) == 34, names
prompts = asyncio.run(m.mcp.list_prompts())
assert sorted(p.name for p in prompts) == ["controlla_rete", "costruisci_rete"], prompts
assert "exec(open(" in m._install_command()
print(f"ok: {len(names)} tool -> {', '.join(names)}")
