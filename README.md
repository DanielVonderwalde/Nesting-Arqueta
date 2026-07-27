# Nesting

Aplicación web para calcular el acomodo de suajes en pliego. Cualquiera en la
empresa llena el formulario, sube el trazo del die-line, y obtiene el reporte
con las cuatro bobinas comparadas. Todo queda guardado y se puede volver a
consultar después.

---

## 1. Probarla en tu computadora

Necesitas Python 3.10 o más nuevo. Para revisar si ya lo tienes, abre la
terminal de VS Code (menú Terminal → New Terminal) y escribe `python3 --version`.

Con la carpeta del proyecto abierta en VS Code, en esa misma terminal:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se abre solo en el navegador, en `http://localhost:8501`. Cada vez que guardes
un cambio en el código, la página se actualiza sola.

Para comprobar que el motor de cálculo funciona sin abrir la interfaz:

```bash
python3 core.py autotest
```

Debe terminar con "Todo bien".

> **Importante:** guarda el proyecto en una carpeta local del disco, no dentro
> de Dropbox, OneDrive ni Google Drive. La base de datos es SQLite y no
> funciona sobre carpetas sincronizadas.

---

## 2. Subirlo a GitHub

Sirve para tener respaldo del código y para conectarlo al hosting. GitHub por
sí solo **no puede correr la aplicación** — solo guarda archivos. El paso 3 es
el que la pone en línea.

Desde VS Code, en la barra lateral izquierda, el icono de las tres ramas
(Source Control):

1. Clic en **Initialize Repository**.
2. Escribe un mensaje, por ejemplo `primera versión`, y clic en **Commit**.
3. Clic en **Publish Branch**. Para la opción gratis de abajo (Streamlit
   Community Cloud) el repo tiene que ser **Public** — no hay problema porque
   la app corre en modo abierto (sin login) de todas formas. Si en vez de
   eso vas a usar Hugging Face Spaces, puedes dejarlo **Private**.

VS Code te pide iniciar sesión en GitHub la primera vez. Si no tienes cuenta,
créala en github.com; es gratis.

El archivo `.gitignore` ya está configurado para que **las cotizaciones y los
trazos de los clientes no se suban a GitHub**. Solo viaja el código.

---

## 3. Publicarlo para toda la empresa

Dos rutas. Empieza por la gratis; si resulta más lata de la que quieres, la de
Hugging Face es más simple y cuesta 5 USD/mes.

### Opción A (gratis): Streamlit Community Cloud + Turso

Streamlit Community Cloud corre la app gratis pero borra el disco cada vez que
se reinicia. Por eso el historial, los reportes y los contornos trazados ya
no viven como archivos — desde este cambio viven en la base de datos — y esa
base tiene que ser una remota que sí persista: **Turso** (SQLite remoto, capa
gratuita permanente: 5 GB, uso comercial permitido, sin tarjeta).

**Crear la base en Turso:**

1. Crea cuenta en [turso.tech](https://turso.tech) e instala su CLI (los
   pasos exactos están en su sitio; en Mac es `curl -sSfL https://get.tur.so/install.sh | bash`).
2. `turso auth login`
3. `turso db create nesting`
4. `turso db show nesting --url` → copia esa URL (empieza con `libsql://`).
5. `turso db tokens create nesting` → copia el token que imprime.

**Conectar la app a esa base**, en local para probar antes de publicar:

```bash
export TURSO_DATABASE_URL="libsql://el-que-copiaste"
export TURSO_AUTH_TOKEN="el-token-que-copiaste"
pip install libsql
python3 core.py autotest
```

Si dice "Todo bien", la app ya está leyendo y escribiendo en Turso en vez de
en el archivo local. Corre `streamlit run app.py` con esas mismas variables
puestas y prueba una cotización completa antes de publicar.

**Publicar:**

1. Repo en GitHub público (paso 2 de arriba).
2. En [share.streamlit.io](https://share.streamlit.io), **New app**, elige el
   repo, rama y `app.py` como archivo principal.
3. En **Advanced settings → Secrets**, pega:
   ```toml
   TURSO_DATABASE_URL = "libsql://el-que-copiaste"
   TURSO_AUTH_TOKEN = "el-token-que-copiaste"
   ```
4. Deploy. La dirección queda fija (`https://<algo>.streamlit.app`) y el
   historial sobrevive a los reinicios porque vive en Turso, no en el disco
   de la app.

> Nota: `libsql` (el paquete que habla con Turso) no está en
> `requirements.txt` como obligatorio porque su instalación falla en
> versiones de Python muy nuevas que todavía no tienen su instalador listo
> (rompería `pip install -r requirements.txt` para todos, no solo para quien
> usa Turso). Streamlit Community Cloud usa una versión de Python donde sí
> instala bien; si tu máquina local falla al instalarlo, no es necesario para
> correr la app en local sin Turso — solo hace falta cuando defines
> `TURSO_DATABASE_URL`.

### Opción B (de paga, más simple): Hugging Face Spaces

No requiere tocar nada del código ni configurar Turso: el almacenamiento
persistente de pago guarda el disco tal cual, así que los reportes, contornos
e historial siguen siendo archivos y sobreviven solos.

1. Crea cuenta en huggingface.co.
2. Arriba a la derecha: **New** → **Space**.
3. Llena así:
   - Space name: `nesting` (la dirección queda `https://<tu-usuario>-nesting.hf.space`)
   - License: la que prefieras
   - Space SDK: **Streamlit**
   - Visibility: la que prefieras (puede ser Private)
4. En la pestaña **Files** del Space, clic en **Add file** → **Upload files**
   y arrastra: `app.py`, `core.py`, `auth.py`, `requirements.txt`, y la carpeta
   `engine` completa.
5. Espera unos dos minutos a que diga **Running**.
6. Para que el historial no se borre en cada reinicio: **Settings** →
   **Persistent storage** → contrata el plan de 5 USD al mes, y en
   **Settings** → **Variables and secrets** agrega `NESTING_DATA_DIR` = `/data`.

Sin ese último paso la app funciona igual pero el historial se pierde en cada
reinicio.

---

## 4. Ponerle login más adelante

Ya está programado y apagado. Abre `auth.py` y sigue las instrucciones de
hasta arriba: es cambiar una línea y dar de alta a la gente. Cuando esté
prendido, el nombre de quien corrió cada cotización queda guardado en el
historial.

---

## Qué hace la aplicación

**Nueva cotización.** Cliente, 1UP, proyecto, ancho y alto del blank, tipo de
pegue, y el PDF o la foto del die-line. Traza el contorno real del suaje —no la
caja envolvente— y prueba todos los acomodos en las cuatro bobinas.

**Reuso de trazos.** Cada contorno trazado se guarda en la base de datos con
su número de 1UP. Si alguien vuelve a correr el 1310, ya no necesita el PDF:
escribe el número y listo.

**Verificación.** Compara la medida trazada contra la que capturaste y avisa en
verde, amarillo o rojo. Una diferencia de más de 5 mm casi siempre significa
que se tomó el contorno equivocado, y la aplicación lo dice en vez de dejarte
mandar un suaje malo a producción.

**Historial.** Buscador por cliente, 1UP o proyecto, con descarga a CSV. Cada
cotización tiene su propia dirección (`?id=...`) que se puede pegar en un
correo o en WhatsApp.

---

## Estándares de planta

Fijos en el código, en `core.py`. No se ajustan para ganar piezas, porque están
puestos por construcción de suaje y registro de prensa:

| Concepto | Valor |
|---|---|
| Separación entre cajas | 10 mm |
| Margen superior (pinza) | 20 mm |
| Margen inferior | 10 mm |
| Márgenes izquierdo y derecho | 10 mm |

Bobinas: Grande 710, Mediano 610, Chico 450, Extra chico 355 mm. El ancho se
recorta libre hasta 1040 mm.

Si algún día cambian, se editan en `core.py`, en la sección
"Estándares de la planta".

---

## Los archivos

| Archivo | Qué es |
|---|---|
| `app.py` | La interfaz: formulario, resultados, historial |
| `core.py` | El trabajo real: base de datos, trazado, cálculo. Se puede probar solo |
| `auth.py` | Control de acceso. Apagado por ahora |
| `engine/` | El motor de nesting y el trazador. No conviene editarlo |
| `data/` | Solo espacio de trabajo temporal del motor. No se sube a GitHub. El historial, los reportes HTML y los contornos trazados viven en la base de datos (SQLite local, o Turso si está configurado), no aquí |
