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

# --- elimina
assert m.delete_node(b["path"]) == {"deleted": "/project1/level1"}
assert fake_td.op("/project1/level1") is None

# --- TouchDesigner spento
server.shutdown()
server.server_close()
try:
    m.td_status()
    raise AssertionError("mancava l'errore di collegamento")
except RuntimeError as e:
    assert "non risponde" in str(e), e

# --- il server MCP registra i tool
tools = asyncio.run(m.mcp.list_tools())
names = sorted(t.name for t in tools)
assert len(names) == 13, names
assert "exec(open(" in m._install_command()
print(f"ok: {len(names)} tool -> {', '.join(names)}")
