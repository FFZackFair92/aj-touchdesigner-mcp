"""Verifica dal vivo, con TouchDesigner aperto e il ponte installato:

    uv run --script check_live.py      (oppure: python check_live.py)

Lavora dentro un COMP temporaneo /project1/_aj_mcp_prova e alla fine lo elimina:
il resto del progetto non viene toccato.
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
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

    e = m.node_errors(BOX)
    print(f"errori nel COMP di prova: {e['errors'] or 'nessuno'}")
    assert m.list_nodes(BOX)["count"] == 2
finally:
    m.delete_node(BOX)
print("tutto ok, COMP di prova eliminato")
