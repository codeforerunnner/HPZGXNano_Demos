# HP ZGX Nano Demos

[English](#english) | [Castellano](#castellano)

---

<a id="english"></a>
## English

Unified repository containing all **HP ZGX Nano (GB10)** local AI demonstrations and the fleet monitoring portal.

### Single-Command Installation

On an HP ZGX Nano unit (Ubuntu ARM64):

```bash
git clone https://github.com/codeforerunnner/HPZGXNano_Demos.git
cd HPZGXNano_Demos
bash install.sh
```

The root `install.sh` script installs all three components in a single pass:
1. **Partner Kit (`zgx-partner-kit/`)**: system dependencies, `uv`, public demos, and models.
2. **Monitoring & Fleet Portal (`fleet-console/`)**: `sudoers` configuration and `zgx-launcher.service` systemd service on port `18888`.
3. **HP ZGX Nano Collective Showcase (`hp-zgx-fury-showcase-v3.2.0-nano.3/`)**: links `$HOME/hp-zgx-nano`, builds/downloads resident models (`gpt-oss-120b`, `Qwen3.6-35B-A3B`, `FLUX.2-klein-4B`, `Kokoro-82M`), and enables Free Mode, Presenter Mode, **Heimdall**, and **Voice Studio** on port `18808`.

### Documentation & Screenshots

See [Guide & Screenshots](docs/README.md) for visual documentation of:
- **HP ZGX Nano - Fleet Console** (`:18888`)
- **HP ZGX Nano - Free Mode** (`:18808/free`)
- **Heimdall**, **Voice Studio**, and **Presentations**

### Repository Structure

- `install.sh`: unified single-command installer.
- `fleet-console/`: standalone **Fleet & Monitoring Portal (`:18888`)** (`launcher_web.py`, `install.sh`, `README.md`) with CPU/GPU/RAM/disk telemetry, global token/EUR meter, and node controls.
- `hp-zgx-fury-showcase-v3.2.0-nano.3/`: main showcase application (`showcase_app`), Free Mode, Presenter Mode, **Heimdall** (multi-turn orchestrator with IPv4 web search, local script execution, downloadable `.pptx` decks, app generation, vision analysis, translation, and WAV/MP3 audio synthesis), and **Voice Studio** (automatic Spanish/English detection with Piper `es_carlos` and Kokoro-82M `am_michael`/`af_heart`).
- `zgx-partner-kit/`: public demos installer and runtime (`portable.py`, `public_services.py`, etc.).
- `docs/`: bilingual documentation and screenshots.

### Notes

- Model weights (`*.gguf`, `*.onnx`, `*.safetensors`) and virtual environments (`.venv/`) are excluded via `.gitignore`.

---

<a id="castellano"></a>
## Castellano

Repositorio unificado con todas las demos de IA local y el portal de monitorizacion y gestion de flota para **HP ZGX Nano (GB10)**.

### Instalacion en un solo comando

En una unidad HP ZGX Nano (Ubuntu ARM64):

```bash
git clone https://github.com/codeforerunnner/HPZGXNano_Demos.git
cd HPZGXNano_Demos
bash install.sh
```

El script `install.sh` instala los tres bloques de una sola vez:
1. **Partner Kit (`zgx-partner-kit/`)**: dependencias del sistema, `uv`, demos y modelos publicos.
2. **Portal de Monitorizacion y Flota (`fleet-console/`)**: permisos `sudoers` y servicio systemd `zgx-launcher.service` en el puerto `18888`.
3. **HP ZGX Nano Collective Showcase (`hp-zgx-fury-showcase-v3.2.0-nano.3/`)**: enlaza `$HOME/hp-zgx-nano`, compila/descarga los modelos residentes (`gpt-oss-120b`, `Qwen3.6-35B-A3B`, `FLUX.2-klein-4B`, `Kokoro-82M`) y deja listos Free Mode, Presenter Mode, **Heimdall** y **Voice Studio** en el puerto `18808`.

### Documentacion y capturas de pantalla

Consulta la [Guia y Capturas](docs/README.md) para ver el detalle y las capturas de:
- **HP ZGX Nano - Fleet Console** (`:18888`)
- **HP ZGX Nano - Free Mode** (`:18808/free`)
- **Heimdall**, **Voice Studio** y **Presentations**

### Estructura del repositorio

- `install.sh`: instalador unificado de todo el proyecto.
- `fleet-console/`: **Portal de Monitorizacion y Flota (`:18888`)** independiente (`launcher_web.py`, `install.sh`, `README.md`) con telemetria CPU/GPU/RAM/disco, contador global de tokens/EUR y control de nodos.
- `hp-zgx-fury-showcase-v3.2.0-nano.3/`: aplicacion principal (`showcase_app`), Free Mode, Presenter Mode, **Heimdall** (orquestador multi-turno con busqueda web IPv4, ejecucion de scripts locales, presentaciones `.pptx` descargables, generacion de aplicaciones, analisis visual, traduccion y sintesis de audio WAV/MP3) y **Voice Studio** (deteccion automatica de idioma espanol/ingles con Piper `es_carlos` y Kokoro-82M `am_michael`/`af_heart`).
- `zgx-partner-kit/`: instalador y runtime de las demos publicas (`portable.py`, `public_services.py`, etc.).
- `docs/`: documentacion bilingue y capturas de pantalla.

### Notas

- Los pesos de modelos (`*.gguf`, `*.onnx`, `*.safetensors`) y entornos virtuales (`.venv/`) estan excluidos en `.gitignore`.
