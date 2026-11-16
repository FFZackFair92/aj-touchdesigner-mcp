"""Finto TouchDesigner per i test offline (test_offline.py e la CI).

Imita quel poco del modulo `td` che il ponte usa e serve le callbacks di
td_bridge.py su una porta HTTP locale, come farebbe un Web Server DAT.
Non sostituisce una prova con TouchDesigner vero.
"""
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
)


class Par:
    def __init__(self, owner, name, value, style="Float", page="Common"):
        self.owner, self.name, self.val = owner, name, value
        self.label, self.style, self.mode, self.readOnly = name.title(), style, "ParMode.CONSTANT", False
        self.page = types.SimpleNamespace(name=page)
        self.pulsed = 0

    def eval(self):
        return self.val

    def pulse(self):
        self.pulsed += 1


class Pars:
    """Come in TouchDesigner: `nodo.par.nome = valore` imposta il valore."""

    def __setattr__(self, name, value):
        current = self.__dict__.get(name)
        if isinstance(current, Par) and not isinstance(value, Par):
            current.val = value
        else:
            self.__dict__[name] = value


class Connector:
    def __init__(self, owner):
        self.owner, self.links = owner, []

    def connect(self, other):
        self.links.append(other.owner)
        other.links.append(self.owner)


class Node:
    family, suffix, comp = "TOP", "TOP", False
    defaults = {}

    def __init__(self, parent, name):
        self.parent_node, self.name = parent, name
        self.nodeX = self.nodeY = 0
        self.children = []
        self.comment = ""
        self.text = ""
        self.par = Pars()
        for k, v in {"active": True, **self.defaults}.items():
            setattr(self.par, k, Par(self, k, v))
        self.inputConnectors = [Connector(self), Connector(self)]
        self.outputConnectors = [Connector(self)]
        self.width, self.height = 1280, 720
        self.numSamples, self.numRows, self.numCols = 1, 0, 0

    @property
    def path(self):
        if self.parent_node is None:
            return "/"
        base = self.parent_node.path
        return f"{base}{self.name}" if base == "/" else f"{base}/{self.name}"

    @property
    def OPType(self):
        return type(self).__name__

    @property
    def isCOMP(self):
        return self.comp

    @property
    def inputs(self):
        return [n for c in self.inputConnectors for n in c.links]

    @property
    def outputs(self):
        return [n for c in self.outputConnectors for n in c.links]

    def pars(self):
        return [p for p in vars(self.par).values() if isinstance(p, Par)]

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def chans(self):
        return [types.SimpleNamespace(name="chan1")]

    def op(self, name):
        return next((c for c in self.children if c.name == name), None)

    def create(self, cls, name=None):
        if not self.comp:
            raise RuntimeError("non e' un COMP")
        base = cls.__name__[: -len(cls.suffix)]
        if not name:
            n = 1
            while self.op(f"{base}{n}"):
                n += 1
            name = f"{base}{n}"
        node = cls(self, name)
        self.children.append(node)
        return node

    def findChildren(self, depth=1):
        out = []
        for c in self.children:
            out.append(c)
            if depth > 1:
                out += c.findChildren(depth=depth - 1)
        return out

    def destroy(self):
        self.parent_node.children.remove(self)

    def saveByteArray(self, ext):
        return bytearray(PNG_1X1)


def _kind(name, family, comp=False, **defaults):
    return type(name, (Node,), {"family": family, "suffix": family, "comp": comp, "defaults": defaults})


noiseTOP = _kind("noiseTOP", "TOP", period=1.0, resolutionw=1280, resolutionh=720)
levelTOP = _kind("levelTOP", "TOP", contrast=1.0, brightness1=1.0)
constantCHOP = _kind("constantCHOP", "CHOP", value0=0.0)
textDAT = _kind("textDAT", "DAT")
webserverDAT = _kind("webserverDAT", "DAT", port=9980, callbacks="")
baseCOMP = _kind("baseCOMP", "COMP", comp=True)

ROOT = baseCOMP(None, "")
ROOT.create(baseCOMP, "project1")


def op(path):
    node = ROOT
    for part in [p for p in str(path).split("/") if p]:
        node = node.op(part)
        if node is None:
            return None
    return node


def ops(*patterns):
    return [n for n in (op(p) for p in patterns) if n is not None]


app = types.SimpleNamespace(version="099", build="2026.10000")
project = types.SimpleNamespace(name="finto.toe", folder="C:/finto", cookRate=60.0)


def install():
    """Registra questo modulo come `td`, cosi' td_bridge.py lo importa."""
    sys.modules["td"] = sys.modules[__name__]


def serve(bridge, port=0):
    """Avvia un finto Web Server DAT che gira le richieste a bridge.onHTTPRequest."""

    class Handler(BaseHTTPRequestHandler):
        def _go(self):
            length = int(self.headers.get("Content-Length") or 0)
            request = {"method": self.command, "uri": self.path, "pars": {},
                       "clientAddress": self.client_address[0], "data": self.rfile.read(length)}
            request.update(self.headers.items())
            response = bridge.onHTTPRequest(None, request, {"statusCode": 200, "statusReason": "OK", "data": ""})
            data = response["data"].encode("utf-8")
            self.send_response(response["statusCode"], response["statusReason"])
            self.send_header("Content-Type", response.get("content-type", "text/plain"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET = _go

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
