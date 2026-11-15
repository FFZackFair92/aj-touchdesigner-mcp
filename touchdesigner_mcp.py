# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""AJ TouchDesigner MCP: server MCP che pilota TouchDesigner.

Parla via HTTP locale con il ponte installato nel progetto (td_bridge.py, un
Web Server DAT sulla porta 9990). Installazione del ponte: vedi README.

Config (variabili d'ambiente, tutte facoltative):
  AJ_TD_URL    indirizzo del ponte      (default http://127.0.0.1:9990)
  AJ_TD_TOKEN  token                    (default: letto da ~/.aj-mcp/touchdesigner.token)
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:  # mcp SDK 2.x
    from mcp.server.mcpserver import Image, MCPServer as FastMCP
except ImportError:  # mcp SDK 1.x
    from mcp.server.fastmcp import FastMCP, Image

HERE = Path(__file__).resolve().parent
URL = os.environ.get("AJ_TD_URL", "http://127.0.0.1:9990").rstrip("/")
TOKEN_FILE = Path.home() / ".aj-mcp" / "touchdesigner.token"

mcp = FastMCP("touchdesigner")


def _token() -> str:
    env = os.environ.get("AJ_TD_TOKEN")
    if env:
        return env.strip()
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError(
            f"Token non trovato in {TOKEN_FILE}: installa prima il ponte in TouchDesigner (vedi README)."
        ) from None


def call(cmd: str, **args: Any) -> Any:
    """Manda un comando al ponte e ritorna il risultato, o solleva RuntimeError."""
    body = json.dumps({"cmd": cmd, "args": {k: v for k, v in args.items() if v is not None}}).encode()
    req = urllib.request.Request(
        URL + "/", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-AJ-Token": _token()},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            reply = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = e.reason
        raise RuntimeError(f"Il ponte ha rifiutato la richiesta ({e.code}): {detail}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"TouchDesigner non risponde su {URL}: apri il progetto con il ponte aj_mcp installato. ({e})"
        ) from None
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "errore sconosciuto"))
    return reply["result"]


# ------------------------------------------------------------------- lettura
@mcp.tool()
def td_status() -> dict:
    """Verifica il collegamento: versione di TouchDesigner, progetto aperto, fps."""
    return call("status")


@mcp.tool()
def list_nodes(path: str = "/project1", depth: int = 1) -> dict:
    """Elenca i nodi dentro un COMP (nome, tipo, famiglia, posizione).

    depth 1 = solo i figli diretti; fino a 6 per scendere nei sotto-COMP.
    """
    return call("list_nodes", path=path, depth=depth)


@mcp.tool()
def node_info(path: str) -> dict:
    """Dettagli di un nodo: ingressi, uscite, errori e, secondo la famiglia,
    risoluzione (TOP), canali (CHOP) o righe/colonne (DAT)."""
    return call("node_info", path=path)


@mcp.tool()
def get_parameters(path: str, filter: str = "") -> dict:
    """Parametri di un nodo con valore, pagina e stile. `filter` cerca nel nome
    o nell'etichetta (es. "res" per la risoluzione)."""
    return call("get_parameters", path=path, filter=filter)


@mcp.tool()
def node_errors(path: str = "/project1") -> dict:
    """Errori e avvisi di un nodo e di tutto quello che contiene."""
    return call("node_errors", path=path)


@mcp.tool()
def list_operator_types(family: str = "") -> dict:
    """Tipi di operatore disponibili in questa versione di TouchDesigner, da
    usare in create_node. family: TOP, CHOP, SOP, DAT, COMP, MAT, POP o vuoto."""
    return call("list_operator_types", family=family)


@mcp.tool()
def top_preview(path: str) -> Image:
    """Immagine di uscita di un TOP, per controllare a occhio il risultato."""
    r = call("top_preview", path=path)
    return Image(data=base64.b64decode(r["png_base64"]), format="png")


# ----------------------------------------------------------------- scrittura
@mcp.tool()
def create_node(op_type: str, parent: str = "/project1", name: str = "",
                x: int | None = None, y: int | None = None) -> dict:
    """Crea un nodo. op_type e' il nome Python del tipo: noiseTOP, levelTOP,
    constantCHOP, textDAT, baseCOMP... (vedi list_operator_types)."""
    return call("create_node", op_type=op_type, parent=parent, name=name or None, x=x, y=y)


@mcp.tool()
def delete_node(path: str) -> dict:
    """Elimina un nodo (e il suo contenuto, se e' un COMP). Non si annulla da qui."""
    return call("delete_node", path=path)


@mcp.tool()
def connect_nodes(source: str, target: str, target_input: int = 0, source_output: int = 0) -> dict:
    """Collega l'uscita di `source` all'ingresso di `target` (stessa famiglia)."""
    return call("connect_nodes", source=source, target=target,
                target_input=target_input, source_output=source_output)


@mcp.tool()
def set_parameters(path: str, values: dict[str, Any]) -> dict:
    """Imposta uno o piu' parametri di un nodo, per nome Python del parametro:
    {"resolutionw": 1280, "resolutionh": 720}. Ritorna i valori riletti."""
    return call("set_parameters", path=path, values=values)


@mcp.tool()
def pulse_parameter(path: str, name: str) -> dict:
    """Preme un parametro di tipo pulse (es. "reset", "start")."""
    return call("pulse_parameter", path=path, name=name)


@mcp.tool()
def run_python(code: str) -> dict:
    """Esegue codice Python dentro TouchDesigner, per tutto quello che gli altri
    tool non coprono. Disponibili: td, op, ops, project, app. Assegna a `result`
    il valore da restituire; cio' che stampi con print torna in `stdout`."""
    return call("run_python", code=code)


def _install_command() -> str:
    bridge_dir = str(HERE).replace("\\", "/")
    return (f'exec(open(r"{bridge_dir}/install_bridge.py", encoding="utf-8").read(), '
            f'{{"BRIDGE_DIR": r"{bridge_dir}"}})')


if __name__ == "__main__":
    if "--install-command" in sys.argv:
        print("Incolla questa riga nella Textport di TouchDesigner (Alt+T) e premi Invio:\n")
        print(_install_command())
    else:
        mcp.run()
