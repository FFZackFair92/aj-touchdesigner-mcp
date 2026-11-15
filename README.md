# AJ TouchDesigner MCP

Server MCP che pilota TouchDesigner a parole: crea e collega nodi, legge e
cambia parametri, controlla gli errori, guarda l'uscita dei TOP ed esegue
Python nel progetto. Scritto da zero, stesso stampo del Resolume MCP e del
MadMapper MCP.

```
Claude  <--stdio-->  touchdesigner_mcp.py  <--HTTP locale :9990-->  td_bridge.py (Web Server DAT nel progetto)
```

> **Stato:** i test offline passano contro un finto TouchDesigner (`fake_td.py`).
> La prova con TouchDesigner vero si fa con `check_live.py`: finche' non l'hai
> lanciata, considera il ponte non verificato sulla tua versione.

## 1. Installa uv

```powershell
winget install --id=astral-sh.uv -e
```

## 2. Installa il ponte in TouchDesigner

Apri il tuo progetto, poi da PowerShell nella cartella di questo repository:

```powershell
uv run --script touchdesigner_mcp.py --install-command
```

Stampa una riga: incollala nella **Textport** di TouchDesigner (Alt+T) e premi
Invio. Crea il COMP `/project1/aj_mcp` (un Text DAT con il codice del ponte e un
Web Server DAT sulla porta 9990) e il token in `~/.aj-mcp/touchdesigner.token`.
Salva il progetto. Per aggiornare il ponte rilancia la stessa riga.

Il ponte va installato in ogni progetto che vuoi pilotare.

## 3. Registra il server in Claude

In `claude_desktop_config.json`, dentro `mcpServers`:

```json
"touchdesigner": {
  "command": "uv",
  "args": ["run", "--script", "C:\\percorso\\touchdesigner-mcp\\touchdesigner_mcp.py"]
}
```

Riavvia Claude.

## 4. Prova

```powershell
uv run --script check_live.py
```

Crea un COMP temporaneo, ci prova ogni funzione e lo elimina.

## Tool

**Lettura** — `td_status` · `list_nodes(path, depth)` · `node_info(path)` ·
`get_parameters(path, filter)` · `node_errors(path)` · `list_operator_types(family)` ·
`top_preview(path)` (immagine di uscita di un TOP)

**Scrittura** — `create_node(op_type, parent, name, x, y)` · `delete_node(path)` ·
`connect_nodes(source, target, target_input, source_output)` ·
`set_parameters(path, values)` · `pulse_parameter(path, name)`

**Tutto il resto** — `run_python(code)`: codice Python dentro TouchDesigner, con
`td`, `op`, `ops`, `project`, `app`. Quello che assegni a `result` torna indietro.

## Sicurezza

Il ponte esegue codice Python nel progetto, quindi:

- risponde solo a richieste che arrivano dallo stesso computer;
- richiede il token di `~/.aj-mcp/touchdesigner.token` (header `X-AJ-Token`);
- senza token non parte nessun comando.

Chi ha quel file puo' pilotare TouchDesigner: non condividerlo.

## Configurazione

| Variabile | Default | A cosa serve |
|---|---|---|
| `AJ_TD_URL` | `http://127.0.0.1:9990` | indirizzo del ponte |
| `AJ_TD_TOKEN` | letto dal file | token, se vuoi passarlo a mano |

Per cambiare porta: aggiungi `"BRIDGE_PORT": 9995` al dizionario della riga di
installazione e imposta `AJ_TD_URL` di conseguenza.

## File

- `touchdesigner_mcp.py` — il server MCP
- `td_bridge.py` — il ponte, gira dentro TouchDesigner
- `install_bridge.py` — installa o aggiorna il ponte nel progetto aperto
- `check_live.py` — prova dal vivo
- `fake_td.py`, `test_offline.py` — test senza TouchDesigner (li usa la CI)

## Licenza

MIT, vedi [LICENSE](LICENSE).
