# Contexto del proyecto

App web para calcular el acomodo (nesting) de suajes de cartón plegadizo en
pliego. La usa gente de planta y de cotización, no programadores.

## Correr

```bash
pip install -r requirements.txt
streamlit run app.py          # interfaz en localhost:8501
python3 core.py autotest      # prueba el motor sin interfaz
```

## Arquitectura

- `app.py` — interfaz Streamlit. Tres vistas: nueva cotización, historial, y
  una cotización guardada (por query param `?id=`).
- `core.py` — todo el trabajo real. Sin interfaz, testeable solo. Base de datos
  SQLite, trazado y cálculo. Los scripts del motor se invocan por subprocess.
- `auth.py` — control de acceso. `MODO = "abierto"` ahora mismo. Ya está escrito
  el login por usuario y por clave única; se prende cambiando esa constante.
- `engine/` — motor de nesting y trazador de die-lines. Scripts CLI que reciben
  JSON por argv y devuelven JSON. **No editar sin necesidad**, están probados.
- `data/` — solo espacio de trabajo temporal del motor (subprocess escribe ahi
  y `core.py` lee de vuelta en la misma corrida). El historial, los reportes
  HTML (`reporte_html`) y los contornos trazados (tabla `contornos`) viven en
  la base de datos, no en archivos — necesario porque el hosting gratis borra
  el disco en cada reinicio. En `.gitignore`. `NESTING_DATA_DIR` cambia esta
  ruta.
- Base de datos intercambiable: SQLite local por default, o Turso (remoto) si
  `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` estan en el ambiente. `libsql` no
  esta en requirements.txt como obligatorio porque no tiene wheel para
  versiones de Python muy nuevas todavia; se instala aparte (`pip install
  libsql`) solo si vas a usar Turso.

## Reglas del dominio que NO se deben romper

Estos valores son de construcción de suaje y registro de prensa. Nunca los
ajustes para que quepan más piezas — mandaría un suaje que la prensa no sostiene:

- Separación entre cajas 10 mm
- Margen superior (pinza) 20 mm
- Margen inferior 10 mm
- Márgenes izquierdo y derecho 10 mm

Bobinas (alto fijo): Grande 710, Mediano 610, Chico 450, Extra chico 355 mm.
El ancho se recorta libre hasta 1040 mm máximo de prensa.

Tres métricas distintas que no se deben confundir:

- `aprov_bobina_pct` / `merma_bobina_pct` — cuánto de la bobina usa el pliego.
  **Esta es la de costeo.**
- `utilization_pct` / `waste_pct` — cuánto del pliego llenan las cajas. Es la
  calidad del nesting.
- `utilization_total_pct` — cajas contra la bobina consumida.

Los acomodos que se pasan de la bobina por pocos milímetros se muestran
marcados, nunca se ocultan: unos mm a veces se arreglan en el suaje y esa
decisión es del usuario. Pero su aprovechamiento nunca se presenta como real.

## Detalles que ya mordieron

- El alto que captura la gente suele ser el panel del cuerpo, no el blank
  completo. La app compara contra el trazado y semaforiza la diferencia.
- Un `height_match_delta_mm` de 1–2 mm es grosor de línea. Arriba de 5 mm casi
  siempre significa que se trazó el contorno equivocado.
- Los contornos se guardan como `data/outlines/AF1UP####.json` y se reutilizan
  por número de 1UP, para no resubir el PDF cada vez.
- SQLite no funciona en carpetas de Dropbox, OneDrive ni Google Drive.

## Pendiente

~~Mover reportes HTML y contornos de archivos a columnas de la base~~ — hecho.
`core.py` ya guarda todo en la base de datos (columna `reporte_html`, tabla
`contornos`) y se conecta a Turso cuando `TURSO_DATABASE_URL` /
`TURSO_AUTH_TOKEN` estan configurados; si no, usa SQLite local igual que
antes. Falta la parte manual, fuera del código: crear la base en Turso
(`turso db create`), publicar el repo (público) en GitHub, desplegar en
Streamlit Community Cloud y poner esas dos variables en sus secrets. Pasos
detallados en el README, sección "3. Publicarlo para toda la empresa".

Alternativa pagada y más simple si Turso da lata: Hugging Face Spaces con
almacenamiento persistente, 5 USD/mes — no requiere tocar código.

Después de eso: prender el login por persona en `auth.py`.
