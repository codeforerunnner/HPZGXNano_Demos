# HP ZGX Nano Demos

Repositorio con el codigo completo de las demos y consola de flota para **HP ZGX Nano (GB10)**:

## Estructura

- `hp-zgx-fury-showcase-v3.2.0-nano.3/`: aplicacion principal (`showcase_app`), Free Mode, Presenter Mode, **Heimdall** (orquestador multi-turno con busqueda web IPv4, ejecucion de scripts locales, presentaciones descargables en `.pptx`, generacion de aplicaciones, analisis visual, traduccion y sintesis de audio WAV/MP3) y **Voice Studio** (deteccion automatica de idioma espanol/ingles con Piper `es_carlos` y Kokoro-82M `am_michael`/`af_heart`).
- `zgx-partner-kit/`: consola web de gestion de flota (`launcher_web.py`), medidor de tokens y coste equivalente en EUR por nodo y flota, comprobaciones previas de requisitos y scripts de arranque.

## Notas

- Los pesos de modelos (`*.gguf`, `*.onnx`, `*.safetensors`) y entornos virtuales (`.venv/`) estan excluidos en `.gitignore`.
