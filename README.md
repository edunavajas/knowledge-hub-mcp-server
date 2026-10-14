# knowledge-hub-mcp

Servidor [MCP](https://modelcontextprotocol.io) **stdio** de un solo fichero Python que
conecta cualquier cliente MCP (Claude Code, Codex, Cursor, OpenCode…) con tu sistema de
conocimiento autoalojado: los **webhooks RAG de n8n** y la **API de Outline**.

Forma parte de la infraestructura que se levanta con Coolify en la serie self-hosted de
YouTube: en lugar de pegar documentación en el chat, el agente consulta directamente tu
base de conocimiento indexada.

No necesita `pip install`: es Python puro con la librería estándar.

## Arquitectura

```
cliente MCP  ──stdio──▶  knowledge-hub-mcp  ──curl──▶  webhooks RAG de n8n
(Claude Code,                               ──curl──▶  API de Outline
 Codex, Cursor,
 OpenCode…)
```

- El servidor habla **JSON-RPC sobre stdio** (el transporte MCP estándar para procesos locales).
- Las llamadas HTTP se hacen con **`curl` vía `subprocess`** de forma deliberada: las claves
  de API y los cuerpos JSON grandes **nunca aparecen en `argv`** (se pasan por `--config` y
  por el `stdin`), evitando fugas por la tabla de procesos.
- **El servidor no lleva credenciales.** Se leen en arranque de un fichero de entorno.

## Herramientas

Expone **5 herramientas**:

| Herramienta | Argumentos | Backend | Qué hace |
| --- | --- | --- | --- |
| `knowledge_search` | `query`, `limit` (1–20, por defecto 5), `source_type` (`outline`\|`repo`\|`all`) | n8n `POST /webhook/rag-search` | Búsqueda semántica sobre conocimiento curado y documentación del repositorio. |
| `knowledge_read` | `document_id` | Outline `POST /api/documents.info` | Lee el Markdown completo de un documento por su ID. |
| `knowledge_health` | — | n8n `GET /webhook/rag-health` | Recuentos y estado del índice, sin contenido ni vectores. |
| `knowledge_index` | — | n8n `POST /webhook/rag-index` | Lanza un reindexado asíncrono de la colección de Outline. |
| `knowledge_index_repository` | — | n8n `POST /webhook/rag-index-repo` | Escanea el Markdown del workspace y lo indexa como conocimiento técnico del repo. |

## Requisitos

- **python3** (3.9+). Sin dependencias: solo la librería estándar.
- **curl** (se usa para todas las llamadas HTTP).
- **git** — solo para `install.sh`.
- **Node.js 18+** — solo para la ruta de instalación vía npm.

## Instalación

### Opción A — npm global (sin clonar)

El repositorio es público:

```sh
npm install -g github:edunavajas/knowledge-hub-mcp
knowledge-hub-mcp --version
```

### Opción B — install.sh

```sh
git clone https://github.com/edunavajas/knowledge-hub-mcp.git "$HOME/.local/share/knowledge-hub-mcp"
sh "$HOME/.local/share/knowledge-hub-mcp/install.sh"
```

`install.sh` es idempotente: clona en `${KNOWLEDGE_HUB_HOME:-$HOME/.local/share/knowledge-hub-mcp}`,
crea un symlink `${KNOWLEDGE_HUB_BIN:-$HOME/.local/bin}/knowledge-hub-mcp` al fichero Python, lo
marca como ejecutable e imprime las rutas resueltas. Si lo vuelves a lanzar, actualiza el checkout
con `git pull --ff-only`.

## Configuración

Las credenciales viven en un **fichero de entorno**. La ruta por defecto es
`~/.config/knowledge-hub/knowledge.env`; se parsea al arrancar y cada `KEY=value` se aplica
**solo si esa clave no está ya definida** en el entorno del proceso (el entorno manda).

Empieza copiando la plantilla:

```sh
mkdir -p ~/.config/knowledge-hub
cp .env.example ~/.config/knowledge-hub/knowledge.env
$EDITOR ~/.config/knowledge-hub/knowledge.env
```

Contenido:

```
N8N_URL=https://n8n.tu-dominio.com
RAG_SEARCH_API_KEY=tu-clave-rag
OUTLINE_URL=https://outline.tu-dominio.com
OUTLINE_API_KEY=tu-clave-outline
KNOWLEDGE_HUB_ENV_FILE=
KNOWLEDGE_HUB_WORKSPACE=
KNOWLEDGE_HUB_PROJECT=
```

Todas las variables que leen el servidor y el instalador:

| Variable | Usada por | Por defecto | Propósito |
| --- | --- | --- | --- |
| `KNOWLEDGE_HUB_ENV_FILE` | servidor | `~/.config/knowledge-hub/knowledge.env` | Fichero de entorno desde el que cargar credenciales. |
| `KNOWLEDGE_HUB_WORKSPACE` | servidor | directorio actual (`$PWD`) | Raíz que escanea `knowledge_index_repository`. |
| `KNOWLEDGE_HUB_PROJECT` | servidor | basename del workspace | Slug usado en `collection`, `canonical_url` y `metadata.repository`. |
| `KNOWLEDGE_HUB_PYTHON` | shim npm | `python3`, luego `python` | Intérprete de Python que arranca el shim de Node. |
| `N8N_URL` | servidor | *(vacío)* | URL base de los webhooks RAG. |
| `OUTLINE_URL` | servidor | *(vacío)* | URL base de la instancia de Outline. |
| `RAG_SEARCH_API_KEY` | servidor | *(vacío)* | Se envía como `X-RAG-API-Key` a n8n. |
| `OUTLINE_API_KEY` | servidor | *(vacío)* | Se envía como `Authorization: Bearer` a Outline. |
| `KNOWLEDGE_HUB_REPO` | install.sh | `https://github.com/edunavajas/knowledge-hub-mcp.git` | Remoto git a clonar. |
| `KNOWLEDGE_HUB_HOME` | install.sh | `$HOME/.local/share/knowledge-hub-mcp` | Ubicación del checkout. |
| `KNOWLEDGE_HUB_BIN` | install.sh | `$HOME/.local/bin` | Dónde se crea el symlink `knowledge-hub-mcp`. |

> **El servidor no lleva credenciales embebidas.** Todo secreto se configura en el env file.

## Configuración para clientes MCP

Aviso general: los clientes con interfaz gráfica (Cursor, Claude Desktop) arrancan los
servidores MCP con un `PATH` mínimo, que normalmente **no incluye** `~/.local/bin` ni el bin
global de npm. Usa siempre una **ruta absoluta** al comando.

### Claude Code

Usa el comando `claude mcp add`. El `--` separa las opciones de Claude de la orden y sus
argumentos; las variables van con `--env`:

```sh
claude mcp add --transport stdio knowledge_hub \
  --env KNOWLEDGE_HUB_WORKSPACE="$PWD" \
  -- /ruta/absoluta/.local/bin/knowledge-hub-mcp
```

También puedes declararlo en `.mcp.json` (ámbito de proyecto):

```json
{
  "mcpServers": {
    "knowledge_hub": {
      "type": "stdio",
      "command": "/ruta/absoluta/.local/bin/knowledge-hub-mcp",
      "args": [],
      "env": {
        "KNOWLEDGE_HUB_WORKSPACE": "/ruta/absoluta/a/tu/repo"
      }
    }
  }
}
```

Comprueba el estado con `claude mcp list` o `/mcp`.

### Codex

Codex usa TOML en `~/.codex/config.toml` (o `.codex/config.toml` a nivel de proyecto). La
tabla es `[mcp_servers.<nombre>]`, con `command`, `args` y `env`:

```toml
[mcp_servers.knowledge_hub]
command = "/ruta/absoluta/.local/bin/knowledge-hub-mcp"
args = []
enabled = true
cwd = "/ruta/absoluta/a/tu/repo"

[mcp_servers.knowledge_hub.env]
KNOWLEDGE_HUB_WORKSPACE = "/ruta/absoluta/a/tu/repo"
```

### Cursor

Cursor usa JSON en `~/.cursor/mcp.json` con la clave contenedora `mcpServers`:

```json
{
  "mcpServers": {
    "knowledge_hub": {
      "command": "/Users/<tu-usuario>/.local/bin/knowledge-hub-mcp",
      "env": {
        "KNOWLEDGE_HUB_WORKSPACE": "/Users/<tu-usuario>/repos/mi-proyecto"
      }
    }
  }
}
```

Si `python3` no es resoluble desde el entorno de Cursor, llama al intérprete directamente:
`"command": "/usr/bin/python3"` con
`"args": ["/Users/<tu-usuario>/.local/share/knowledge-hub-mcp/knowledge_hub_mcp.py"]`.

### OpenCode

OpenCode usa `opencode.json` con la clave contenedora **`mcp`** (no `mcpServers`), `type`
`"local"` y el comando como **array**; las variables van en **`environment`**:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "knowledge_hub": {
      "type": "local",
      "command": ["knowledge-hub-mcp"],
      "enabled": true,
      "environment": {
        "KNOWLEDGE_HUB_WORKSPACE": "/ruta/absoluta/a/tu/repo"
      }
    }
  }
}
```

Si el bin no está en el `PATH` de OpenCode, usa la ruta absoluta dentro del array:
`"command": ["/ruta/absoluta/.local/bin/knowledge-hub-mcp"]`.

## Ejemplos de uso

Una vez conectado, pídeselo en lenguaje natural al agente:

- **Buscar**: «Busca en la base de conocimiento cómo configuramos Coolify.»
  → `knowledge_search` con `{"query": "configuración de Coolify", "limit": 5, "source_type": "all"}`
- **Leer**: «Lee el documento `abc123` y resúmelo.»
  → `knowledge_read` con `{"document_id": "abc123"}`
- **Estado**: «¿Cuántos documentos hay indexados?»
  → `knowledge_health` sin argumentos
- **Indexar Outline**: «Reindexa la colección de Outline.»
  → `knowledge_index` sin argumentos
- **Indexar el repo**: «Indexa la documentación de este repositorio.»
  → `knowledge_index_repository` sin argumentos

## `--print-config`

Imprime la configuración resuelta **redactando los secretos** (útil para depurar y para
enseñarlo en cámara sin exponer claves):

```sh
knowledge-hub-mcp --print-config
```

Ejemplo de salida:

```
version: 0.1.0
env_file: /home/tu/.config/knowledge-hub/knowledge.env
env_file_exists: true
workspace_root: /home/tu/repos/mi-proyecto
project_slug: mi-proyecto
n8n_url: https://n8n.tu-dominio.com
outline_url: https://outline.tu-dominio.com
rag_search_api_key: <REDACTED>
outline_api_key: <REDACTED>
```

Si `env_file_exists: false`, el fichero no está donde el servidor lo busca: créalo en
`~/.config/knowledge-hub/knowledge.env` o apunta `KNOWLEDGE_HUB_ENV_FILE` a su ruta real.

## Problemas frecuentes

- **`RAG bridge is not configured` / `Outline bridge is not configured`.** El env file falta o
  las claves están vacías. Compruébalo con `knowledge-hub-mcp --print-config`.
- **`command not found` en el cliente.** Los clientes con GUI usan un `PATH` mínimo: usa la
  ruta absoluta al binario (ver arriba).

## Licencia

MIT — ver [LICENSE](LICENSE).
