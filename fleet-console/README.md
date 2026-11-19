# HP ZGX Nano - Fleet Console

[English](#english) | [Castellano](#castellano)

---

<a id="english"></a>
## English

Standalone monitoring, hardware telemetry, token/EUR accounting, and fleet management portal for **HP ZGX Nano** units.

### Contents
- `launcher_web.py`: self-contained standard-library Python web server and API (`0.0.0.0:18888`).
- `install.sh`: configures `sudoers` permissions and installs/starts the `zgx-launcher.service` systemd unit.

### Standalone Installation
```bash
bash install.sh
```
Web URL: `http://<IP-ZGX>:18888/login` (default login: `admin` / `Admin321`).

---

<a id="castellano"></a>
## Castellano

Modulo independiente de monitorizacion, telemetria hardware, medicion de tokens/EUR y control de flota para unidades **HP ZGX Nano**.

### Contenido
- `launcher_web.py`: servidor web y API (`0.0.0.0:18888`) en Python estandar sin dependencias externas.
- `install.sh`: configura permisos `sudoers` e instala/arranca el servicio systemd `zgx-launcher.service`.

### Instalacion independiente
```bash
bash install.sh
```
Acceso web: `http://<IP-ZGX>:18888/login` (usuario por defecto `admin` / `Admin321`).
