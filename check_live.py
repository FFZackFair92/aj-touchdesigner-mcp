"""Verifica dal vivo, con TouchDesigner aperto e il ponte installato:

    uv run --script check_live.py      (oppure: python check_live.py)

Lavora dentro un COMP temporaneo /project1/_aj_mcp_prova e alla fine lo elimina:
il resto del progetto non viene toccato.
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.10,<3"]
# ///
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import touchdesigner_mcp as m  # noqa: E402

BOX = "/project1/_aj_mcp_prova"

s = m.td_status()
print(f"collegato: TouchDesigner {s['touchdesigner']}, progetto {s['project']}, ponte {s['bridge']}")
print(f"tipi TOP disponibili: {m.list_operator_types('TOP')['count']}")

if m.run_python(f"result = op('{BOX}') is not None")["result"]:
    m.delete_node(BOX)
m.create_node("baseCOMP", name="_aj_mcp_prova")
try:
    a = m.create_node("noiseTOP", parent=BOX, name="rumore")
    b = m.create_node("levelTOP", parent=BOX, name="livello", x=200)
    m.connect_nodes(a["path"], b["path"])
    assert m.node_info(b["path"])["inputs"] == [a["path"]], "collegamento non riuscito"
    print("crea + collega: ok")

    r = m.set_parameters(b["path"], {"brightness1": 1.2})
    assert abs(r["values"]["brightness1"] - 1.2) < 1e-6, r
    assert m.get_parameters(b["path"], "brightness1")["count"] >= 1
    print("parametri: ok")

    img = m.top_preview(b["path"])
    assert img.data[:8] == b"\x89PNG\r\n\x1a\n", "l'anteprima non e' un PNG"
    print(f"anteprima TOP: ok ({len(img.data)} byte)")

    c = m.build_node("levelTOP", parent=BOX, name="finale", params={"opacity": 0.8}, inputs=[b["path"]])
    assert c["inputs"] == [b["path"]], c
    m.set_parameters(c["path"], {"brightness1": {"expr": "1 + 0.1"}})
    assert abs(m.get_parameters(c["path"], "brightness1")["parameters"][0]["value"] - 1.1) < 1e-6
    assert [n["name"] for n in m.find_nodes(BOX, name="fin*")["nodes"]] == ["finale"]
    m.set_node_state(c["path"], color=[0.2, 0.6, 1.0], comment="prova AJ")
    m.auto_layout(BOX)
    print("build_node + espressione + ricerca + layout: ok")

    t = m.create_node("tableDAT", parent=BOX, name="tabella")
    m.write_dat(t["path"], rows=[["a", "b"], ["1", "2"]])
    assert m.read_dat(t["path"])["rows"] == [["a", "b"], ["1", "2"]]
    k = m.create_node("constantCHOP", parent=BOX, name="valori")
    assert m.read_chop(k["path"])["samples"] >= 1
    print("DAT e CHOP: ok")

    print(f"timeline: {m.timeline()}")
    print(f"nodo piu' lento: {m.get_performance(BOX, top=1)['slowest']}")
    assert m.python_help("OP", filter="cook")["members"], "python_help vuoto"
    r = m.batch([{"cmd": "copy_node", "args": {"path": a["path"], "name": "rumore_b"}},
                 {"cmd": "rename_node", "args": {"path": BOX + "/rumore_b", "new_name": "rumore_c"}}])
    assert r["steps"] == 2, r
    print("python_help + batch: ok")

    # 1.2
    r = m.sample_node(a["path"])
    assert r["channels"] and r["samples"][0]["rgba"], r
    assert m.sample_node(k["path"])["channels"], "sample_node CHOP vuoto"
    h = m.op_help("noiseTOP", filter="period")
    assert h["count"] >= 1 and m.run_python("result = op('/project1/_aj_help_tmp') is None")["result"]
    g = m.glsl_shader("shader", parent=BOX)
    assert g["compiled"], g
    bad = m.glsl_shader("shader", parent=BOX, code="out vec4 fragColor; void main(){ ERRORE }")
    assert not bad["compiled"], "lo shader sbagliato doveva dare errore"
    m.custom_parameters(BOX, "add", name="velocita", min=0, max=10, default=2)
    assert any(p["name"] == "Velocita" for pg in m.custom_parameters(BOX)["pages"] for p in pg["pars"])
    m.custom_parameters(BOX, "remove", name="velocita")
    snap = m.network_snapshot(BOX)
    assert any(n["path"] == "finale" and "opacity" in n["params"] for n in snap["nodes"]), snap
    print("1.2 (sample_node, op_help, glsl_shader, custom_parameters, network_snapshot): ok")

    e = m.node_errors(BOX)
    print(f"errori nel COMP di prova: {e['errors'] or 'nessuno'}")
    assert m.list_nodes(BOX)["count"] == 8
finally:
    m.delete_node(BOX)
print("tutto ok, COMP di prova eliminato")
