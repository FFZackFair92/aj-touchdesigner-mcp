"""Ponte AJ MCP per TouchDesigner: callbacks di un Web Server DAT.

Questo file gira DENTRO TouchDesigner. Lo installa install_bridge.py, che lo
copia in un Text DAT e lo collega a un Web Server DAT sulla porta 9990.

Protocollo: POST / con corpo JSON {"cmd": "...", "args": {...}} e header
X-AJ-Token. Risposta JSON {"ok": true, "result": ...} oppure
{"ok": false, "error": "..."}.

Sicurezza: il ponte esegue codice Python nel progetto, quindi accetta solo
richieste dallo stesso computer e solo con il token salvato in
~/.aj-mcp/touchdesigner.token.
"""
import base64
import contextlib
import fnmatch
import hmac
import inspect
import io
import ipaddress
import json
import os
import traceback

import td

TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".aj-mcp", "touchdesigner.token")
BRIDGE_VERSION = "1.1.0"


# ---------------------------------------------------------------- utilita'
def _token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _is_local(address):
    """Vero se la richiesta arriva da questo computer. TouchDesigner passa
    l'indirizzo in forme diverse secondo la versione: con o senza porta, IPv4
    o IPv6, anche IPv4 mappato su IPv6 (::ffff:127.0.0.1)."""
    text = str(address or "").strip().lower()
    if text.startswith("["):  # [::1]:54321
        text = text[1:].split("]")[0]
    elif text.count(":") == 1:  # 127.0.0.1:54321
        text = text.split(":")[0]
    if text == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def _header(request, name):
    """Gli header arrivano come chiavi del dizionario: confronto senza maiuscole."""
    name = name.lower()
    for k, v in request.items():
        if isinstance(k, str) and k.lower() == name:
            return v if isinstance(v, str) else str(v)
    return ""


def _plain(v):
    """Rende un valore di TouchDesigner serializzabile in JSON."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    path = getattr(v, "path", None)
    return path if isinstance(path, str) else str(v)


def _node(path):
    o = td.op(path)
    if o is None:
        raise ValueError(f"nodo non trovato: {path}")
    return o


def _summary(o):
    return {
        "path": o.path,
        "name": o.name,
        "type": o.OPType,
        "family": o.family,
        "x": o.nodeX,
        "y": o.nodeY,
    }


def _par_info(p):
    try:
        value = _plain(p.eval())
    except Exception as e:  # un parametro in errore non deve bloccare la lista
        value = f"<errore: {e}>"
    return {
        "name": p.name,
        "label": p.label,
        "page": p.page.name if p.page is not None else "",
        "style": p.style,
        "mode": str(p.mode),
        "value": value,
        "read_only": bool(p.readOnly),
    }


# ----------------------------------------------------------------- comandi
def cmd_status(args):
    return {
        "bridge": BRIDGE_VERSION,
        "touchdesigner": str(td.app.build),
        "project": td.project.name,
        "folder": td.project.folder,
        "fps": td.project.cookRate,
    }


def cmd_list_nodes(args):
    parent = _node(args.get("path", "/project1"))
    depth = max(1, min(int(args.get("depth", 1)), 6))
    nodes = parent.findChildren(depth=depth)
    limit = int(args.get("limit", 300))
    return {
        "parent": parent.path,
        "count": len(nodes),
        "truncated": len(nodes) > limit,
        "nodes": [_summary(o) for o in nodes[:limit]],
    }


def cmd_node_info(args):
    o = _node(args["path"])
    info = _summary(o)
    info.update(
        inputs=[i.path if i is not None else None for i in o.inputs],
        outputs=[x.path for x in o.outputs],
        errors=o.errors(recurse=False),
        warnings=o.warnings(recurse=False),
        is_comp=bool(o.isCOMP),
        children=len(o.children) if o.isCOMP else 0,
    )
    if o.family == "TOP":
        info["resolution"] = [o.width, o.height]
    elif o.family == "CHOP":
        info["channels"] = [c.name for c in o.chans()][:64]
        info["samples"] = o.numSamples
    elif o.family == "DAT":
        info["rows"], info["cols"] = o.numRows, o.numCols
    return info


def cmd_create_node(args):
    parent = _node(args.get("parent", "/project1"))
    if not parent.isCOMP:
        raise ValueError(f"{parent.path} non e' un COMP: non puo' contenere nodi")
    op_type = args["op_type"]
    cls = getattr(td, op_type, None)
    if cls is None:
        raise ValueError(f"tipo di operatore sconosciuto: {op_type} (es. noiseTOP, levelTOP, constantCHOP)")
    name = args.get("name") or None
    x = int(args["x"]) if args.get("x") is not None else None  # validati prima di creare
    y = int(args["y"]) if args.get("y") is not None else None
    o = parent.create(cls, name) if name else parent.create(cls)
    if x is not None:
        o.nodeX = x
    if y is not None:
        o.nodeY = y
    return _summary(o)


SYSTEM_PATHS = ("/", "/project1", "/local", "/sys", "/ui")


def cmd_delete_node(args):
    o = _node(args["path"])
    if o.path in SYSTEM_PATHS:
        raise ValueError(f"{o.path} e' un nodo di sistema: non lo elimino")
    path = o.path
    o.destroy()
    return {"deleted": path}


def cmd_connect_nodes(args):
    src, dst = _node(args["source"]), _node(args["target"])
    out_i, in_i = int(args.get("source_output", 0)), int(args.get("target_input", 0))
    if out_i >= len(src.outputConnectors):
        raise ValueError(f"{src.path} non ha l'uscita {out_i}")
    if in_i >= len(dst.inputConnectors):
        raise ValueError(f"{dst.path} non ha l'ingresso {in_i}")
    src.outputConnectors[out_i].connect(dst.inputConnectors[in_i])
    return {"source": src.path, "target": dst.path, "target_input": in_i}


def cmd_get_parameters(args):
    o = _node(args["path"])
    needle = (args.get("filter") or "").lower()
    pars = [p for p in o.pars() if needle in p.name.lower() or needle in p.label.lower()]
    return {"path": o.path, "count": len(pars), "parameters": [_par_info(p) for p in pars]}


def _set_par(p, value):
    """Valore fisso, oppure {"expr": "..."} per un'espressione Python o
    {"bind": "..."} per un bind a un altro parametro."""
    if isinstance(value, dict):
        if "expr" in value:
            p.expr = str(value["expr"])
            p.mode = td.ParMode.EXPRESSION
        elif "bind" in value:
            p.bindExpr = str(value["bind"])
            p.mode = td.ParMode.BIND
        else:
            raise ValueError(f"{p.name}: usa un valore, {{'expr': ...}} oppure {{'bind': ...}}")
        return
    if str(p.mode) != str(td.ParMode.CONSTANT):
        p.mode = td.ParMode.CONSTANT
    p.val = value


def cmd_set_parameters(args):
    o = _node(args["path"])
    values = args.get("values") or {}
    pars = {}
    for name in values:  # tutti validati prima di toccare qualcosa
        p = getattr(o.par, name, None)
        if p is None:
            raise ValueError(f"{o.path} non ha il parametro '{name}'")
        pars[name] = p
    done = {}
    for name, value in values.items():
        _set_par(pars[name], value)
        done[name] = _plain(pars[name].eval())
    return {"path": o.path, "values": done}


def cmd_pulse_parameter(args):
    o = _node(args["path"])
    p = getattr(o.par, args["name"], None)
    if p is None:
        raise ValueError(f"{o.path} non ha il parametro '{args['name']}'")
    p.pulse()
    return {"path": o.path, "pulsed": p.name}


def cmd_node_errors(args):
    o = _node(args.get("path", "/project1"))
    return {
        "path": o.path,
        "errors": o.errors(recurse=True),
        "warnings": o.warnings(recurse=True),
    }


def cmd_top_preview(args):
    o = _node(args["path"])
    if o.family != "TOP":
        raise ValueError(f"{o.path} non e' un TOP")
    data = bytes(o.saveByteArray(".png"))
    return {"path": o.path, "width": o.width, "height": o.height,
            "png_base64": base64.b64encode(data).decode("ascii")}


def cmd_list_operator_types(args):
    family = (args.get("family") or "").upper()
    families = ("TOP", "CHOP", "SOP", "DAT", "COMP", "MAT", "POP")
    if family and family not in families:
        raise ValueError(f"famiglia sconosciuta: {family} (valide: {', '.join(families)})")
    wanted = (family,) if family else families
    names = sorted(n for n in dir(td) if n.endswith(wanted) and n not in families and n[0].islower())
    return {"family": family or "tutte", "count": len(names), "types": names}


def cmd_run_python(args):
    scope = {"td": td, "op": td.op, "ops": td.ops, "project": td.project, "app": td.app,
             "result": None}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(args["code"], scope)
    return {"result": _plain(scope.get("result")), "stdout": out.getvalue()[-8000:]}


# ------------------------------------------------------- comandi aggiunti (1.1)
def _safe_name(name):
    if not isinstance(name, str) or not name or name.startswith("_"):
        raise ValueError(f"nome non consentito: {name!r}")
    return name


def cmd_find_nodes(args):
    root = _node(args.get("path", "/project1"))
    pattern = (args.get("name") or "*").lower()
    op_type = (args.get("op_type") or "").lower()
    family = (args.get("family") or "").upper()
    depth = max(1, min(int(args.get("depth", 6)), 10))
    limit = int(args.get("limit", 200))
    hits = [o for o in root.findChildren(depth=depth)
            if fnmatch.fnmatchcase(o.name.lower(), pattern)
            and (not op_type or o.OPType.lower() == op_type)
            and (not family or o.family == family)]
    return {"root": root.path, "count": len(hits), "truncated": len(hits) > limit,
            "nodes": [_summary(o) for o in hits[:limit]]}


def cmd_rename_node(args):
    o = _node(args["path"])
    if o.path in SYSTEM_PATHS:
        raise ValueError(f"{o.path} e' un nodo di sistema: non lo rinomino")
    new = str(args["new_name"]).strip()
    if not new:
        raise ValueError("nome nuovo vuoto")
    if o.parent().op(new) is not None:
        raise ValueError(f"esiste gia' un nodo {new} in {o.parent().path}")
    old = o.path
    o.name = new
    return {"old": old, "path": o.path}


def cmd_copy_node(args):
    o = _node(args["path"])
    parent = _node(args["parent"]) if args.get("parent") else o.parent()
    if not parent.isCOMP:
        raise ValueError(f"{parent.path} non e' un COMP")
    name = args.get("name") or None
    new = parent.copy(o, name=name) if name else parent.copy(o)
    new.nodeX = int(args["x"]) if args.get("x") is not None else o.nodeX + 200
    new.nodeY = int(args["y"]) if args.get("y") is not None else o.nodeY
    return _summary(new)


def cmd_disconnect_nodes(args):
    o = _node(args["target"])
    which = int(args.get("target_input", -1))
    connectors = list(o.inputConnectors)
    if which >= len(connectors):
        raise ValueError(f"{o.path} non ha l'ingresso {which}")
    chosen = connectors if which < 0 else [connectors[which]]
    for c in chosen:
        c.disconnect()
    return {"target": o.path, "disconnected": "tutti" if which < 0 else which}


FLAGS = ("bypass", "display", "render", "lock", "viewer")


def cmd_set_node_state(args):
    o = _node(args["path"])
    color = args.get("color")
    if color is not None and len(color) != 3:
        raise ValueError("color vuole 3 numeri tra 0 e 1 (r, g, b)")
    for flag in FLAGS:
        if args.get(flag) is not None and not hasattr(o, flag):
            raise ValueError(f"{o.path} ({o.family}) non ha il flag {flag}")
    if args.get("x") is not None:
        o.nodeX = int(args["x"])
    if args.get("y") is not None:
        o.nodeY = int(args["y"])
    if color is not None:
        o.color = tuple(float(c) for c in color)
    if args.get("comment") is not None:
        o.comment = str(args["comment"])
    for flag in FLAGS:
        if args.get(flag) is not None:
            setattr(o, flag, bool(args[flag]))
    state = _summary(o)
    state.update(color=_plain(o.color), comment=o.comment,
                 **{f: bool(getattr(o, f)) for f in FLAGS if hasattr(o, f)})
    return state


def cmd_auto_layout(args):
    """Dispone i nodi di un COMP da sinistra a destra seguendo i collegamenti."""
    comp = _node(args.get("path", "/project1"))
    if not comp.isCOMP:
        raise ValueError(f"{comp.path} non e' un COMP")
    dx, dy = int(args.get("spacing_x", 200)), int(args.get("spacing_y", 150))
    nodes = [c for c in comp.children if c.name != "aj_mcp"]
    inside = {n.path for n in nodes}
    column = {}

    def depth(n, seen=()):
        if n.path in column:
            return column[n.path]
        ups = [i for i in n.inputs if i is not None and i.path in inside and i.path not in seen]
        column[n.path] = 0 if not ups else 1 + max(depth(i, seen + (n.path,)) for i in ups)
        return column[n.path]

    for n in nodes:
        depth(n)
    rows = {}
    for n in sorted(nodes, key=lambda n: (column[n.path], n.nodeY * -1, n.name)):
        r = rows.get(column[n.path], 0)
        rows[column[n.path]] = r + 1
        n.nodeX, n.nodeY = column[n.path] * dx, -r * dy
    return {"path": comp.path, "nodes": len(nodes), "columns": len(rows)}


def cmd_read_dat(args):
    o = _node(args["path"])
    if o.family != "DAT":
        raise ValueError(f"{o.path} non e' un DAT")
    limit = max(1, min(int(args.get("max_rows", 200)), 5000))
    rows = [[c.val for c in o.row(i)] for i in range(min(o.numRows, limit))]
    return {"path": o.path, "rows_total": o.numRows, "cols": o.numCols,
            "truncated": o.numRows > limit, "rows": rows, "text": o.text[:20000]}


def cmd_write_dat(args):
    o = _node(args["path"])
    if o.family != "DAT":
        raise ValueError(f"{o.path} non e' un DAT")
    text, rows = args.get("text"), args.get("rows")
    if (text is None) == (rows is None):
        raise ValueError("indica text oppure rows (non entrambi)")
    if not (text or rows) and not args.get("confirm_empty"):
        raise ValueError(f"svuoterei {o.path}: ripeti con confirm_empty=true se e' voluto")
    if rows is not None:
        o.clear()
        for r in rows:
            o.appendRow([str(c) for c in r])
    else:
        o.text = str(text)
    return {"path": o.path, "rows": o.numRows, "cols": o.numCols}


def cmd_edit_dat(args):
    o = _node(args["path"])
    if o.family != "DAT":
        raise ValueError(f"{o.path} non e' un DAT")
    find, repl = str(args["find"]), str(args.get("replace", ""))
    hits = o.text.count(find)
    if hits == 0:
        raise ValueError(f"testo da sostituire non trovato in {o.path}")
    if hits > 1 and not args.get("replace_all"):
        raise ValueError(f"il testo compare {hits} volte: allarga find o usa replace_all=true")
    o.text = o.text.replace(find, repl)
    return {"path": o.path, "replaced": hits}


def cmd_read_chop(args):
    o = _node(args["path"])
    if o.family != "CHOP":
        raise ValueError(f"{o.path} non e' un CHOP")
    limit = max(1, min(int(args.get("max_samples", 64)), 4096))
    chans = list(o.chans())[: int(args.get("max_channels", 64))]
    return {"path": o.path, "samples": o.numSamples, "rate": _plain(getattr(o, "rate", None)),
            "channels": {c.name: [round(float(v), 6) for v in list(c.vals)[:limit]] for c in chans}}


def cmd_exec_node_method(args):
    o = _node(args["path"])
    name = _safe_name(args["method"])
    fn = getattr(o, name, None)
    if not callable(fn):
        raise ValueError(f"{o.path} non ha il metodo {name}")
    return {"path": o.path, "method": name,
            "result": _plain(fn(*(args.get("args") or []), **(args.get("kwargs") or {})))}


def cmd_python_help(args):
    """Membri e documentazione di una classe di td (OP, Par, noiseTOP...) o di un nodo."""
    target = str(args["name"])
    obj = td.op(target) if target.startswith("/") else getattr(td, target, None)
    if obj is None:
        raise ValueError(f"non trovato: {target} (usa un nome di td, es. 'OP', o il percorso di un nodo)")
    needle = (args.get("filter") or "").lower()
    members = []
    for n in dir(obj):
        if n.startswith("_") or needle not in n.lower():
            continue
        try:
            v = getattr(obj, n)
        except Exception:
            continue
        doc = (inspect.getdoc(v) or "").split("\n")[0] if callable(v) else ""
        members.append({"name": n, "kind": "metodo" if callable(v) else "attributo", "doc": doc[:200]})
    return {"name": target, "doc": (inspect.getdoc(obj) or "")[:4000], "members": members[:400]}


def cmd_get_performance(args):
    root = _node(args.get("path", "/project1"))
    top = max(1, min(int(args.get("top", 15)), 100))
    rows = []
    for o in root.findChildren(depth=10):
        cpu = float(getattr(o, "cpuCookTime", 0) or 0)
        gpu = float(getattr(o, "gpuCookTime", 0) or 0)
        rows.append({"path": o.path, "type": o.OPType, "cpu_ms": round(cpu, 3), "gpu_ms": round(gpu, 3),
                     "cooks": int(getattr(o, "totalCooks", 0) or 0)})
    rows.sort(key=lambda r: r["cpu_ms"] + r["gpu_ms"], reverse=True)
    return {"path": root.path, "fps_target": td.project.cookRate, "nodes": len(rows),
            "total_ms": round(sum(r["cpu_ms"] + r["gpu_ms"] for r in rows), 3), "slowest": rows[:top]}


def cmd_timeline(args):
    t = td.op("/").time
    if args.get("rate") is not None:
        t.rate = float(args["rate"])
    if args.get("frame") is not None:
        t.frame = float(args["frame"])
    if args.get("play") is not None:
        t.play = bool(args["play"])
    return {"play": bool(t.play), "frame": _plain(t.frame), "rate": _plain(t.rate),
            "start": _plain(getattr(t, "start", None)), "end": _plain(getattr(t, "end", None))}


def _abs(path, ext):
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not path.lower().endswith(ext):
        raise ValueError(f"il file deve finire in {ext}")
    return path


def cmd_save_tox(args):
    o = _node(args["path"])
    if not o.isCOMP:
        raise ValueError(f"{o.path} non e' un COMP: solo i COMP si salvano in .tox")
    path = _abs(args["file"], ".tox")
    if not os.path.isdir(os.path.dirname(path)):
        raise ValueError(f"cartella non trovata: {os.path.dirname(path)}")
    o.save(path)
    return {"path": o.path, "file": path}


def cmd_load_tox(args):
    parent = _node(args.get("parent", "/project1"))
    if not parent.isCOMP:
        raise ValueError(f"{parent.path} non e' un COMP")
    path = _abs(args["file"], ".tox")
    if not os.path.isfile(path):
        raise ValueError(f"file non trovato: {path}")
    o = parent.loadTox(path)
    if args.get("name"):
        o.name = str(args["name"])
    return _summary(o)


def cmd_save_project(args):
    path = _abs(args["file"], ".toe") if args.get("file") else None
    ok = td.project.save(path) if path else td.project.save()
    return {"saved": bool(ok) if ok is not None else True, "project": td.project.name,
            "folder": td.project.folder}


def cmd_build_node(args):
    """Crea, imposta, collega e controlla in un colpo solo."""
    params = args.get("params") or {}
    created = cmd_create_node(args)
    o = td.op(created["path"])
    try:
        for name in params:
            if getattr(o.par, name, None) is None:
                raise ValueError(f"{o.OPType} non ha il parametro '{name}'")
        for name, value in params.items():
            _set_par(getattr(o.par, name), value)
        for i, src in enumerate(args.get("inputs") or []):
            if src:
                cmd_connect_nodes({"source": src, "target": o.path, "target_input": i})
    except Exception:
        o.destroy()  # niente nodi a meta'
        raise
    with contextlib.suppress(Exception):
        o.cook(force=True)
    info = cmd_node_info({"path": o.path})
    info["params"] = {n: _plain(getattr(o.par, n).eval()) for n in params}
    return info


def _undo_manager():
    ui = getattr(td, "ui", None)
    return getattr(ui, "undo", None)


def cmd_undo(args):
    u = _undo_manager()
    action = "redo" if args.get("redo") else "undo"
    fn = getattr(u, action, None)
    if fn is None:
        raise RuntimeError(f"{action} non disponibile in questa versione di TouchDesigner")
    steps = max(1, min(int(args.get("steps", 1)), 50))
    for _ in range(steps):
        fn()
    return {"action": action, "steps": steps}


def cmd_batch(args):
    """Esegue piu' comandi in fila; si ferma al primo errore."""
    results = []
    for i, step in enumerate(args.get("commands") or []):
        name = step.get("cmd")
        if name in ("batch", "undo"):
            raise ValueError(f"passo {i}: {name} non si puo' usare dentro batch")
        fn = COMMANDS.get(name)
        if fn is None:
            raise ValueError(f"passo {i}: comando sconosciuto {name}")
        try:
            results.append(fn(step.get("args") or {}))
        except Exception as e:
            raise RuntimeError(f"passo {i} ({name}) fallito dopo {i} riusciti: {type(e).__name__}: {e}") from None
    return {"steps": len(results), "results": results}



COMMANDS = {name[4:]: fn for name, fn in list(globals().items()) if name.startswith("cmd_")}


READ_ONLY = {"status", "list_nodes", "node_info", "get_parameters", "node_errors", "top_preview",
             "list_operator_types", "find_nodes", "read_dat", "read_chop", "python_help",
             "get_performance", "undo"}


def _undoable(name, fn, args):
    """I comandi che cambiano il progetto finiscono in un unico passo di
    annullamento: Ctrl+Z in TouchDesigner annulla l'intero comando."""
    u = _undo_manager()
    if name in READ_ONLY or u is None or not hasattr(u, "startBlock"):
        return fn(args)
    u.startBlock(f"AJ MCP {name}")
    try:
        return fn(args)
    finally:
        with contextlib.suppress(Exception):
            u.endBlock()


# ---------------------------------------------------------------- callback
def _reply(response, status, reason, payload):
    response["statusCode"] = status
    response["statusReason"] = reason
    response["content-type"] = "application/json"
    response["data"] = json.dumps(payload)
    return response


def onHTTPRequest(webServerDAT, request, response):
    if not _is_local(request.get("clientAddress")):
        return _reply(response, 403, "Forbidden", {
            "ok": False, "error": f"solo richieste locali (ricevuta da: {request.get('clientAddress')!r})"})
    token = _token()
    if not token or not hmac.compare_digest(_header(request, "X-AJ-Token"), token):
        return _reply(response, 401, "Unauthorized", {"ok": False, "error": "token mancante o errato"})
    if request.get("method") != "POST":
        return _reply(response, 405, "Method Not Allowed", {"ok": False, "error": "usa POST"})
    try:
        raw = request.get("data") or b"{}"
        body = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        fn = COMMANDS.get(body.get("cmd"))
        if fn is None:
            raise ValueError(f"comando sconosciuto: {body.get('cmd')}")
        return _reply(response, 200, "OK", {"ok": True, "result": _undoable(body.get("cmd"), fn, body.get("args") or {})})
    except Exception as e:
        return _reply(response, 200, "OK", {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()[-2000:],
        })
