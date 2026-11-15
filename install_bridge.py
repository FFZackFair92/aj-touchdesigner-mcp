"""Installa il ponte AJ MCP nel progetto TouchDesigner aperto.

Si lancia dalla Textport di TouchDesigner (Alt+T) con la riga che stampa:

    uv run --script touchdesigner_mcp.py --install-command

La riga passa BRIDGE_DIR, la cartella dove sta questo file. Lo script:
  1. crea il token in ~/.aj-mcp/touchdesigner.token, se manca;
  2. crea (o aggiorna) il COMP /project1/aj_mcp con dentro un Text DAT con il
     codice di td_bridge.py e un Web Server DAT sulla porta 9990.
Rilanciarlo aggiorna il ponte senza duplicare niente. Poi salva il progetto.
"""
import os
import secrets

import td

PORT = int(globals().get("BRIDGE_PORT", 9990))
BRIDGE_DIR = globals().get("BRIDGE_DIR") or os.getcwd()
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".aj-mcp", "touchdesigner.token")

# 1. token
os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
if not os.path.exists(TOKEN_FILE) or not open(TOKEN_FILE, encoding="utf-8").read().strip():
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(secrets.token_urlsafe(32))

# 2. nodi
with open(os.path.join(BRIDGE_DIR, "td_bridge.py"), encoding="utf-8") as f:
    code = f.read()

home = td.op("/project1") or td.op("/")
comp = home.op("aj_mcp") or home.create(td.baseCOMP, "aj_mcp")
comp.comment = "Ponte AJ MCP: non eliminare se usi il server MCP"

callbacks = comp.op("callbacks") or comp.create(td.textDAT, "callbacks")
callbacks.text = code
callbacks.nodeX, callbacks.nodeY = 0, -150

server = comp.op("server") or comp.create(td.webserverDAT, "server")
server.par.active = False
server.par.port = PORT
server.par.callbacks = callbacks.name
server.par.active = True

print(f"Ponte AJ MCP pronto in {comp.path}, porta {PORT}. Token in {TOKEN_FILE}.")
print("Salva il progetto (Ctrl+S) per ritrovarlo alla prossima apertura.")
