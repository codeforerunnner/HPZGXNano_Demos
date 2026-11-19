# HP ZGX Nano Demos

Repositorio unificado con todas las demos y el portal de monitorizacion y gestion de flota para **HP ZGX Nano (GB10)**.

## Instalacion unica (un solo comando)

En una unidad HP ZGX Nano (Ubuntu ARM64):

```bash
git clone https://github.com/codeforerunnner/HPZGXNano_Demos.git
cd HPZGXNano_Demos
bash install.sh
```

El script `install.sh` instala de una sola vez:
1. **Portal de Monitorizacion y Flota** (`zgx-partner-kit/`): dependencias del sistema, `uv`, demos y modelos publicos, permisos `sudoers` y servicio `systemd` `zgx-launcher.service` en el puerto `18888`.
2. **HP ZGX Nano Collective Showcase** (`hp-zgx-fury-showcase-v3.2.0-nano.3/`): enlaza `$HOME/hp-zgx-nano`, compila/descarga los modelos (`gpt-oss-120b`, `Qwen3.6-35B-A3B`, `FLUX.2-klein-4B`, `Kokoro-82M`) y deja listos Free Mode, Presenter Mode, **Heimdall** y **Voice Studio** en el puerto `18808`.

## Documentacion y capturas de pantalla

Consulta la [Guia y Capturas](docs/README.md) para ver en detalle el funcionamiento y las capturas de:
- **HP ZGX Nano - Fleet Console** (`:18888`)
- **HP ZGX Nano - Free Mode** (`:18808/free`)
- **Heimdall**, **Voice Studio** y **Presentations**

## Estructura

- `install.sh`: instalador unificado de todo el proyecto.
- `fleet-console/`: **Portal de Monitorizacion y Flota (`:18888`)** separado (`launcher_web.py`, `install.sh`, `README.md`), con telemetria CPU/GPU/RAM/disco, contador global de tokens/EUR y control de nodos.
- `hp-zgx-fury-showcase-v3.2.0-nano.3/`: aplicacion principal (`showcase_app`), Free Mode, Presenter Mode, **Heimdall** (orquestador multi-turno con busqueda web IPv4, ejecucion de scripts locales, presentaciones `.pptx` descargables, generacion de aplicaciones, analisis visual, traduccion y sintesis de audio WAV/MP3) y **Voice Studio** (deteccion automatica de idioma espanol/ingles con Piper `es_carlos` y Kokoro-82M `am_michael`/`af_heart`).
- `zgx-partner-kit/`: instalador y runtime de las demos publicas (`portable.py`, `public_services.py`, etc.).
- `docs/`: documentacion y capturas de pantalla.

## Notas

- Los pesos de modelos (`*.gguf`, `*.onnx`, `*.safetensors`) y entornos virtuales (`.venv/`) estan excluidos en `.gitignore`.
