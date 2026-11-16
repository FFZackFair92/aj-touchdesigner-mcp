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
import hmac
import io
import ipaddress
import json
import os
import traceback

import td

TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".aj-mcp", "touchdesigner.token")
BRIDGE_VERSION = "1.0.0"


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


def cmd_delete_node(args):
    o = _node(args["path"])
    if o.path in ("/", "/project1", "/local", "/sys", "/ui"):
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


def cmd_set_parameters(args):
    o = _node(args["path"])
    done = {}
    for name, value in (args.get("values") or {}).items():
        p = getattr(o.par, name, None)
        if p is None:
            raise ValueError(f"{o.path} non ha il parametro '{name}'")
        p.val = value
        done[name] = _plain(p.eval())
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


COMMANDS = {name[4:]: fn for name, fn in list(globals().items()) if name.startswith("cmd_")}


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
        return _reply(response, 200, "OK", {"ok": True, "result": fn(body.get("args") or {})})
    except Exception as e:
        return _reply(response, 200, "OK", {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()[-2000:],
        })
