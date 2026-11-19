# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.10,<3"]
# ///
"""AJ TouchDesigner MCP: server MCP che pilota TouchDesigner.

Parla via HTTP locale con il ponte installato nel progetto (td_bridge.py, un
Web Server DAT sulla porta 9990). Installazione del ponte: vedi README.

Config (variabili d'ambiente, tutte facoltative):
  AJ_TD_URL    indirizzo del ponte      (default http://127.0.0.1:9990)
  AJ_TD_TOKEN  token                    (default: letto da ~/.aj-mcp/touchdesigner.token)
  AJ_TD_EXE    eseguibile di TouchDesigner per open_touchdesigner (default: cercato da solo)
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.types import ToolAnnotations

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


# -------------------------------------------------------------------- avvio
def _version_key(path: str) -> list[int]:
    digits = "".join(c if c.isdigit() else " " for c in path).split()
    return [int(d) for d in digits]


def find_touchdesigner() -> str | None:
    """Percorso dell'eseguibile di TouchDesigner: AJ_TD_EXE, poi il PATH, poi
    le cartelle di installazione abituali (la versione piu' recente)."""
    env = os.environ.get("AJ_TD_EXE")
    if env:
        return env if os.path.isfile(env) else None
    found = shutil.which("TouchDesigner")
    if found:
        return found
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")]
        hits = [p for r in roots if r
                for p in glob.glob(os.path.join(r, "Derivative", "TouchDesigner*", "bin", "TouchDesigner.exe"))]
    elif sys.platform == "darwin":
        hits = glob.glob("/Applications/TouchDesigner*.app/Contents/MacOS/TouchDesigner")
    else:
        hits = []
    return max(hits, key=_version_key) if hits else None


def _launch(command: list[str]) -> None:
    """Avvia un programma staccato da questo processo, cosi' resta aperto."""
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, creationflags=flags, close_fds=True, **quiet)
    else:
        subprocess.Popen(command, start_new_session=True, close_fds=True, **quiet)


@mcp.tool()
def open_touchdesigner(project: str = "", wait_seconds: int = 90) -> dict:
    """Apre TouchDesigner su questo computer e aspetta che il ponte risponda.

    Se TouchDesigner e' gia' aperto con il ponte attivo non ne apre un altro.
    project: un file .toe da aprire (facoltativo; senza, parte il progetto di
    avvio). Il ponte risponde solo se il progetto aperto lo contiene.
    """
    try:
        return {"opened": False, "note": "TouchDesigner era gia' aperto", **call("status")}
    except RuntimeError as e:
        if "non risponde" not in str(e) and "Token non trovato" not in str(e):
            raise
    exe = find_touchdesigner()
    if exe is None:
        raise RuntimeError("TouchDesigner non trovato: installalo o indica il percorso nella variabile AJ_TD_EXE.")
    command = [exe]
    if project:
        path = os.path.abspath(os.path.expanduser(project))
        if not os.path.isfile(path):
            raise RuntimeError(f"file non trovato: {path}")
        command.append(path)
    _launch(command)
    deadline = time.time() + max(1, min(int(wait_seconds), 300))
    while time.time() < deadline:
        time.sleep(1)
        try:
            return {"opened": True, "executable": exe, **call("status")}
        except RuntimeError:
            continue
    raise RuntimeError(
        f"TouchDesigner e' stato avviato ({exe}) ma il ponte non risponde: "
        "il progetto aperto non contiene il ponte aj_mcp (vedi README, installazione)."
    )


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
    {"resolutionw": 1280, "resolutionh": 720}. Ritorna i valori riletti.

    Un valore puo' anche essere un'espressione, {"period": {"expr": "absTime.seconds"}},
    o un bind, {"value0": {"bind": "op('ctrl').par.Gain"}}. Se un nome e'
    sbagliato non cambia niente."""
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


# ------------------------------------------------------------- ricerca
@mcp.tool()
def find_nodes(path: str = "/project1", name: str = "*", op_type: str = "", family: str = "",
               depth: int = 6, limit: int = 200) -> dict:
    """Cerca nodi dentro un COMP e nei sotto-COMP. name accetta * e ?
    (es. "noise*"); op_type e' il tipo esatto (noiseTOP); family TOP, CHOP, SOP,
    DAT, COMP, MAT o POP."""
    return call("find_nodes", path=path, name=name, op_type=op_type or None,
                family=family or None, depth=depth, limit=limit)


@mcp.tool()
def python_help(name: str, filter: str = "") -> dict:
    """Documentazione Python di TouchDesigner: una classe del modulo td
    ("OP", "Par", "noiseTOP", "tdu"...) o un nodo (percorso). Ritorna la
    descrizione e i membri; filter restringe i membri per nome."""
    return call("python_help", name=name, filter=filter or None)


@mcp.tool()
def get_performance(path: str = "/project1", top: int = 15) -> dict:
    """I nodi che costano di piu' (tempo di cottura CPU e GPU in ms), per
    capire cosa abbassa gli fps."""
    return call("get_performance", path=path, top=top)


# ------------------------------------------------------------- nodi
@mcp.tool()
def build_node(op_type: str, parent: str = "/project1", name: str = "",
               params: dict[str, Any] | None = None, inputs: list[str] | None = None,
               x: int | None = None, y: int | None = None) -> dict:
    """Crea un nodo, imposta i parametri, collega gli ingressi e controlla gli
    errori in una sola chiamata. inputs: percorsi dei nodi da collegare agli
    ingressi 0, 1, ... Se qualcosa fallisce il nodo viene eliminato."""
    return call("build_node", op_type=op_type, parent=parent, name=name or None,
                params=params, inputs=inputs, x=x, y=y)


@mcp.tool()
def rename_node(path: str, new_name: str) -> dict:
    """Rinomina un nodo."""
    return call("rename_node", path=path, new_name=new_name)


@mcp.tool()
def copy_node(path: str, parent: str = "", name: str = "",
              x: int | None = None, y: int | None = None) -> dict:
    """Duplica un nodo (con tutto il contenuto, se e' un COMP), nello stesso
    COMP o in `parent`. Senza x/y la copia va 200 a destra dell'originale."""
    return call("copy_node", path=path, parent=parent or None, name=name or None, x=x, y=y)


@mcp.tool()
def disconnect_nodes(target: str, target_input: int = -1) -> dict:
    """Stacca i collegamenti in ingresso a `target`: uno solo (target_input)
    o tutti (-1)."""
    return call("disconnect_nodes", target=target, target_input=target_input)


@mcp.tool()
def set_node_state(path: str, x: int | None = None, y: int | None = None,
                   color: list[float] | None = None, comment: str | None = None,
                   bypass: bool | None = None, display: bool | None = None,
                   render: bool | None = None, lock: bool | None = None,
                   viewer: bool | None = None) -> dict:
    """Posizione, colore [r, g, b], commento e flag (bypass, display, render,
    lock, viewer) di un nodo. Quello che non indichi resta com'e'."""
    return call("set_node_state", path=path, x=x, y=y, color=color, comment=comment,
                bypass=bypass, display=display, render=render, lock=lock, viewer=viewer)


@mcp.tool()
def auto_layout(path: str = "/project1", spacing_x: int = 200, spacing_y: int = 150) -> dict:
    """Riordina i nodi di un COMP da sinistra a destra seguendo i collegamenti,
    cosi' la rete resta leggibile."""
    return call("auto_layout", path=path, spacing_x=spacing_x, spacing_y=spacing_y)


@mcp.tool()
def exec_node_method(path: str, method: str, args: list[Any] | None = None,
                     kwargs: dict[str, Any] | None = None) -> dict:
    """Chiama un metodo Python di un nodo, es. method="cook" con
    kwargs={"force": true}. Ritorna il valore restituito. Per i parametri
    pulse usa pulse_parameter."""
    return call("exec_node_method", path=path, method=method, args=args, kwargs=kwargs)


# ------------------------------------------------------------- dati
@mcp.tool()
def read_dat(path: str, max_rows: int = 200) -> dict:
    """Contenuto di un DAT: righe e celle (tabelle) e testo (Text DAT, script)."""
    return call("read_dat", path=path, max_rows=max_rows)


@mcp.tool()
def write_dat(path: str, text: str | None = None, rows: list[list[Any]] | None = None,
              confirm_empty: bool = False) -> dict:
    """Riscrive un DAT: `text` per i Text DAT e gli script, `rows` per le
    tabelle. Per svuotarlo serve confirm_empty=true."""
    return call("write_dat", path=path, text=text, rows=rows, confirm_empty=confirm_empty or None)


@mcp.tool()
def edit_dat(path: str, find: str, replace: str = "", replace_all: bool = False) -> dict:
    """Modifica mirata di un DAT: sostituisce `find` con `replace`. Se `find`
    compare piu' volte serve replace_all=true (o un pezzo di testo piu' lungo)."""
    return call("edit_dat", path=path, find=find, replace=replace, replace_all=replace_all or None)


@mcp.tool()
def read_chop(path: str, max_samples: int = 64, max_channels: int = 64) -> dict:
    """Valori dei canali di un CHOP (i primi max_samples campioni di ognuno)."""
    return call("read_chop", path=path, max_samples=max_samples, max_channels=max_channels)


# ------------------------------------------------------------- progetto
@mcp.tool()
def timeline(play: bool | None = None, frame: float | None = None, rate: float | None = None) -> dict:
    """Linea del tempo: play/pausa, vai al fotogramma, cambia la frequenza.
    Senza argomenti legge lo stato."""
    return call("timeline", play=play, frame=frame, rate=rate)


@mcp.tool()
def save_tox(path: str, file: str) -> dict:
    """Salva un COMP in un file .tox, per riusarlo in altri progetti."""
    return call("save_tox", path=path, file=file)


@mcp.tool()
def load_tox(file: str, parent: str = "/project1", name: str = "") -> dict:
    """Carica un file .tox dentro un COMP."""
    return call("load_tox", file=file, parent=parent, name=name or None)


@mcp.tool()
def save_project(file: str = "") -> dict:
    """Salva il progetto .toe (senza file: sul file aperto)."""
    return call("save_project", file=file or None)


@mcp.tool()
def undo(steps: int = 1, redo: bool = False) -> dict:
    """Annulla (o ripete) gli ultimi passi. Ogni comando di questo server che
    cambia il progetto e' un passo solo, batch compreso."""
    return call("undo", steps=steps, redo=redo or None)


@mcp.tool()
def batch(commands: list[dict[str, Any]]) -> dict:
    """Esegue piu' comandi del ponte in fila in una sola chiamata, es.
    [{"cmd": "create_node", "args": {"op_type": "noiseTOP"}},
     {"cmd": "set_parameters", "args": {"path": "/project1/noise1", "values": {"period": 2}}}].
    I nomi sono quelli dei tool (senza td_/open_). Si ferma al primo errore;
    un solo undo annulla tutto."""
    return call("batch", commands=commands)


# ------------------------------------------------------- 1.2: valori vivi, shader, pannelli
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def sample_node(path: str, points: list[list[float]] | None = None, limit: int = 32) -> dict:
    """Valori vivi di qualsiasi nodo. TOP: min/max/media per canale, 'black' se l'immagine e' nera, pixel ai punti
    uv [[0.5, 0.5], ...]. CHOP: min/max/media e primi campioni di ogni canale. SOP/POP: punti, primitive, bounds,
    primi punti e attributi. DAT: righe. Serve per capire se una rete 'reagisce' senza guardare l'anteprima."""
    return call("sample_node", path=path, points=points, limit=limit)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def op_help(op_type: str, filter: str = "") -> dict:
    """Tutti i parametri di un TIPO di operatore (es. 'noiseTOP', 'audiospectrumCHOP') con pagina, stile, default e
    voci dei menu, prima di crearlo. filter cerca nel nome o nell'etichetta."""
    return call("op_help", op_type=op_type, filter=filter or None)


@mcp.tool()
def dat_file(path: str, file: str, action: str = "save") -> dict:
    """Un DAT e un file su disco: save (scrive il DAT nel file, es. per versionare script e shader con git),
    load (legge il file nel DAT), sync (il DAT segue il file e si ricarica quando cambia)."""
    return call("dat_file", path=path, file=file, action=action)


@mcp.tool()
def custom_parameters(path: str, action: str = "list", name: str = "", style: str = "float", page: str = "Custom",
                      label: str = "", default: Any = None, min: float | None = None, max: float | None = None,
                      clamp: bool = False, menu: list[str] | None = None) -> dict:
    """Parametri custom di un COMP, cioe' il pannello di controllo di un componente. action: list | add | remove.
    style: float, int, toggle, pulse, str, menu, rgb, rgba, xy, xyz, file, folder, op, top, chop, header...
    Il nome diventa con l'iniziale maiuscola (regola di TouchDesigner). Poi si collega con set_parameters e
    {"expr": "parent().par.Velocita"}."""
    return call("custom_parameters", path=path, action=action, name=name or None, style=style, page=page,
                label=label or None, default=default, min=min, max=max, clamp=clamp or None, menu=menu)


@mcp.tool()
def cook_node(path: str, recurse: bool = False) -> dict:
    """Forza il ricalcolo di un nodo (recurse: anche quelli a monte) e riporta costo ed errori."""
    return call("cook_node", path=path, recurse=recurse or None)


@mcp.tool()
def glsl_shader(name: str = "glsl1", code: str = "", parent: str = "/project1",
                resolution: list[int] | None = None, inputs: list[str] | None = None) -> dict:
    """Crea o aggiorna un GLSL TOP con il suo pixel shader in un DAT, scrive il codice, compila e restituisce gli
    errori di compilazione. Senza code mette uno shader di prova. Se il codice usa uTime lo collega al tempo.
    Ripeti con il codice corretto finche' compiled e' true, poi sample_node o top_preview per vederlo."""
    return call("glsl_shader", name=name, code=code or None, parent=parent, resolution=resolution, inputs=inputs)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def network_snapshot(path: str = "/project1", depth: int = 1, include_dat_text: bool = False) -> dict:
    """Fotografia testuale di una rete: tipo, posizione, flag, ingressi e SOLO i parametri cambiati dal default
    (espressioni e bind compresi). Per capire una rete a colpo d'occhio, confrontarla prima/dopo o ricostruirla
    altrove con batch."""
    return call("network_snapshot", path=path, depth=depth, include_dat_text=include_dat_text or None)


# ------------------------------------------------------------- prompt
@mcp.prompt()
def controlla_rete(path: str = "/project1") -> str:
    """Controllo di una rete: errori, avvisi, costi e anteprima."""
    return (f"Controlla la rete {path} in TouchDesigner: usa node_errors per errori e avvisi, "
            f"get_performance per i nodi piu' lenti e top_preview sui TOP di uscita. "
            "Riassumi i problemi in ordine di gravita' e proponi le correzioni prima di applicarle.")


@mcp.prompt()
def costruisci_rete(descrizione: str, parent: str = "/project1") -> str:
    """Costruzione guidata di una rete dalla descrizione."""
    return (f"Costruisci in {parent} questa rete TouchDesigner: {descrizione}\n"
            "Procedi cosi': list_operator_types per i nomi giusti; build_node per ogni nodo "
            "(parametri e ingressi insieme); node_errors dopo ogni gruppo; auto_layout alla fine; "
            "top_preview sull'uscita per verificare. Se sbagli, undo.")


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
