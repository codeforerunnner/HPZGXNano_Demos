# HP ZGX Nano - Fleet Console (Portal de Monitorizacion)

Modulo independiente de monitorizacion, telemetria hardware, medicion de tokens/EUR y control de flota para unidades **HP ZGX Nano**.

## Contenido

- `launcher_web.py`: servidor web y API (`0.0.0.0:18888`) en Python estandar sin dependencias externas.
- `install.sh`: configura permisos `sudoers` e instala/arranca el servicio systemd `zgx-launcher.service`.

## Instalacion independiente

```bash
bash install.sh
```

Acceso web: `http://<IP-ZGX>:18888/login` (usuario por defecto `admin` / `Admin321`).
