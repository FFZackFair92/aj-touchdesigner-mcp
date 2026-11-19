# AJ TouchDesigner MCP

Server MCP che pilota TouchDesigner a parole: crea e collega nodi, legge e
cambia parametri, controlla gli errori, guarda l'uscita dei TOP ed esegue
Python nel progetto. Scritto da zero, stesso stampo del Resolume MCP e del
MadMapper MCP.

```
Claude  <--stdio-->  touchdesigner_mcp.py  <--HTTP locale :9990-->  td_bridge.py (Web Server DAT nel progetto)
```

> **Stato:** provato dal vivo con TouchDesigner 2025.33230 su Windows
> (`check_live.py`). I test offline girano contro un finto TouchDesigner
> (`fake_td.py`). Su altre versioni lancia `check_live.py` prima di fidarti.

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

## Tool (41)

**Avvio** — `open_touchdesigner(project, wait_seconds)`: apre TouchDesigner (o un
.toe) e aspetta il ponte; se e' gia' aperto non ne apre un altro

**Lettura** — `td_status` · `list_nodes(path, depth)` · `find_nodes(path, name, op_type, family)`
(name con `*` e `?`) · `node_info(path)` · `get_parameters(path, filter)` ·
`node_errors(path)` · `list_operator_types(family)` · `top_preview(path)` (immagine
di uscita di un TOP) · `read_dat(path)` · `read_chop(path)` · `get_performance(path, top)`
(i nodi che costano di piu' in CPU e GPU) · `python_help(name, filter)` (documentazione
di una classe di `td` o di un nodo)

**Costruzione** — `build_node(op_type, parent, name, params, inputs)` (crea, imposta,
collega e controlla gli errori in un colpo; se fallisce non lascia niente) ·
`create_node(op_type, parent, name, x, y)` · `copy_node` · `rename_node` · `delete_node` ·
`connect_nodes(source, target, target_input, source_output)` · `disconnect_nodes(target, target_input)` ·
`auto_layout(path)` (riordina la rete da sinistra a destra)

**Parametri e stato** — `set_parameters(path, values)`: valori fissi,
`{"expr": "absTime.seconds"}` per un'espressione o `{"bind": "..."}`; se un nome e'
sbagliato non cambia niente · `pulse_parameter(path, name)` ·
`set_node_state(path, x, y, color, comment, bypass, display, render, lock, viewer)` ·
`exec_node_method(path, method, args, kwargs)`

**DAT** — `write_dat(path, text | rows)` (per svuotare serve `confirm_empty`) ·
`edit_dat(path, find, replace, replace_all)` (sostituzione mirata, rifiuta i testi ambigui)

**Progetto** — `timeline(play, frame, rate)` · `save_tox(path, file)` · `load_tox(file, parent)` ·
`save_project(file)`

**Valori vivi e analisi (1.2)** — `sample_node(path, points)`: legge i valori di qualsiasi
nodo, non solo i CHOP: TOP (min/max/media per canale, immagine nera sì/no, pixel ai punti uv),
SOP e POP (punti, primitive, bounds, attributi), DAT, CHOP con statistiche ·
`op_help(op_type)` (tutti i parametri di un tipo con default e voci di menu, prima di crearlo) ·
`network_snapshot(path)` (la rete in testo: solo i parametri cambiati, espressioni e bind,
ingressi e flag) · `cook_node(path, recurse)`

**Shader, pannelli e file (1.2)** — `glsl_shader(name, code)`: GLSL TOP + DAT del pixel
shader, compila e ridà gli errori (collega `uTime` da solo) · `custom_parameters(path, action)`:
aggiunge, elenca e toglie parametri custom di un COMP (float, int, toggle, pulse, menu, rgb,
file, op…) · `dat_file(path, file, action)`: salva un DAT su disco, lo ricarica o lo tiene
sincronizzato con il file (script e shader sotto git)

**Annulla e lotti** — ogni comando che cambia il progetto e' un solo passo di
annullamento (Ctrl+Z in TouchDesigner) · `undo(steps, redo)` ·
`batch(commands)`: piu' comandi in una chiamata, si ferma al primo errore, un solo undo

**Tutto il resto** — `run_python(code)`: codice Python dentro TouchDesigner, con
`td`, `op`, `ops`, `project`, `app`. Quello che assegni a `result` torna indietro.

**Prompt** — `costruisci_rete(descrizione)` · `controlla_rete(path)`

> I tool aggiunti nella 1.1 e nella 1.2 sono provati contro il finto TouchDesigner;
> `check_live.py` li prova dal vivo. Dopo l'aggiornamento rilancia la
> riga di installazione del ponte in ogni progetto (passo 2).

## Confronto (novembre 2026)

- **TDMCP di Derivative (ufficiale, beta, 26 tool)**: gira dentro TouchDesigner senza ponte. Dalla 1.2 qui ci sono
  anche le sue funzioni che mancavano: valori vivi di TOP/SOP/POP/DAT (`sample_node`), parametri di un tipo prima di
  crearlo (`op_help`), DAT sincronizzati con file (`dat_file`). In più: costruzione con annullamento (`build_node`,
  `batch`, un solo Ctrl+Z), shader GLSL con errori di compilazione, parametri custom e fotografia della rete.
- **8beeeaaat/touchdesigner-mcp (14)**, **Envoy/Embody (~47)**, **TD_MCP (39)**, **MindDesigner/tdmcp**: coperture
  simili o più strette; nessuno unisce valori vivi, shader e pannelli custom.

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
| `AJ_TD_EXE` | cercato da solo | eseguibile di TouchDesigner per `open_touchdesigner` |

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
