"""
Nucleo del sistema de nesting: base de datos, trazado y calculo.

Este archivo NO tiene interfaz. app.py lo usa para todo el trabajo real,
asi que se puede probar solo desde la terminal:

    python3 core.py autotest
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime

# --- Rutas -----------------------------------------------------------------
# DATA_DIR se puede cambiar con una variable de entorno. En Hugging Face
# Spaces con almacenamiento persistente se pone en /data.
#
# En Streamlit Community Cloud el disco es temporal (se borra en cada
# reinicio), asi que ahi TURSO_DATABASE_URL / TURSO_AUTH_TOKEN apuntan a una
# base remota (Turso) y esa es la que persiste de verdad. Los reportes HTML
# y los contornos trazados viven como columnas de esa base, no como archivos,
# por la misma razon. Las carpetas de abajo solo se usan como espacio de
# trabajo temporal mientras corre el motor (subprocess): se escriben y se
# leen de vuelta en la misma corrida, nunca se dependen de ellas despues.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("NESTING_DATA_DIR", os.path.join(BASE_DIR, "data"))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")

REPORTS_DIR = os.path.join(DATA_DIR, "reports")
OUTLINES_DIR = os.path.join(DATA_DIR, "outlines")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "nesting.db")

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

for _d in (DATA_DIR, REPORTS_DIR, OUTLINES_DIR, UPLOADS_DIR):
    os.makedirs(_d, exist_ok=True)

# --- Estandares de la planta ------------------------------------------------
# Valores fijos por construccion de suaje y registro de prensa.
# No se tocan para ganar piezas.

GAP_MM = 10
MARGIN_TOP_MM = 20
MARGIN_BOTTOM_MM = 10
MARGIN_LEFT_MM = 10
MARGIN_RIGHT_MM = 10

BOBINAS = {
    "Grande": 710,
    "Mediano": 610,
    "Chico": 450,
    "Extra chico": 355,
}

TIPOS_PEGUE = ["Fondo automatico", "Pegado lineal", "Sin pegue", "Otro"]


# --- Utilidades -------------------------------------------------------------

def slug(texto):
    """Quita acentos y espacios para usarlo en nombres de archivo."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")
    return t[:60] or "sin-nombre"


def normaliza_1up(valor):
    """Acepta '1310', 'af1up1310' o 'AF1UP1310' y siempre devuelve AF1UP1310."""
    v = str(valor).strip().upper().replace(" ", "")
    if not v:
        return ""
    if v.startswith("AF1UP"):
        return v
    solo_digitos = re.sub(r"\D", "", v)
    return f"AF1UP{solo_digitos}" if solo_digitos else v


def ruta_contorno(oneup):
    """Ruta de trabajo temporal donde el motor de trazado escribe el JSON.
    Es solo un bloc de notas de la corrida: lo que persiste de verdad es la
    fila en la tabla `contornos` (ver guarda_contorno / contorno_guardado)."""
    return os.path.join(OUTLINES_DIR, f"{normaliza_1up(oneup)}.json")


def contorno_guardado(oneup):
    """Devuelve los puntos del contorno si ya lo trazamos antes, si no None."""
    cx = conecta()
    try:
        cur = cx.execute(
            "SELECT puntos FROM contornos WHERE oneup = ?", (normaliza_1up(oneup),)
        )
        f = cur.fetchone()
    finally:
        cx.close()
    return json.loads(f[0]) if f else None


def _corre_script(nombre, cfg):
    """Ejecuta un script del motor y devuelve su salida JSON."""
    r = subprocess.run(
        [sys.executable, os.path.join(ENGINE_DIR, nombre), json.dumps(cfg)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{nombre} fallo:\n{r.stderr[-1500:]}")
    return json.loads(r.stdout)


# --- Base de datos ----------------------------------------------------------

ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS cotizaciones (
        id            TEXT PRIMARY KEY,
        creada        TEXT NOT NULL,
        cliente       TEXT NOT NULL,
        oneup         TEXT NOT NULL,
        proyecto      TEXT NOT NULL,
        capturo       TEXT,
        tipo_pegue    TEXT,
        ancho_mm      REAL,
        alto_mm       REAL,
        notas         TEXT,
        trazo_ancho   REAL,
        trazo_alto    REAL,
        delta_alto    REAL,
        eficiencia    REAL,
        bobina        TEXT,
        piezas        INTEGER,
        pliego_w      REAL,
        pliego_h      REAL,
        aprovechamiento REAL,
        merma         REAL,
        pzas_m2       REAL,
        reporte       TEXT,
        reporte_html  TEXT,
        resultado     TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cliente ON cotizaciones(cliente)",
    "CREATE INDEX IF NOT EXISTS idx_oneup   ON cotizaciones(oneup)",
    # Contornos trazados por numero de 1UP. Antes vivian como archivos en
    # data/outlines/; ahora la fila de la base es la fuente de verdad (el
    # archivo local sigue existiendo un instante, solo como bloc de notas del
    # motor), para que el 1UP se reutilice aunque el disco se haya borrado.
    """
    CREATE TABLE IF NOT EXISTS contornos (
        oneup       TEXT PRIMARY KEY,
        puntos      TEXT NOT NULL,
        actualizado TEXT
    )
    """,
]


def _es_turso():
    return bool(TURSO_URL)


def conecta():
    """Abre una conexion. Local (SQLite en disco) o remota (Turso), segun
    TURSO_DATABASE_URL. Ambas hablan DB-API 2 (execute/fetchall/commit/close),
    asi que el resto del archivo no necesita saber cual es cual."""
    if _es_turso():
        try:
            import libsql
        except ImportError as e:
            raise RuntimeError(
                "TURSO_DATABASE_URL esta configurado pero falta el paquete "
                "'libsql'. Corre: pip install -r requirements.txt"
            ) from e
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    return sqlite3.connect(DB_PATH)


def _columnas(cursor):
    return [d[0] for d in cursor.description]


def _filas(cursor):
    cols = _columnas(cursor)
    return [dict(zip(cols, fila)) for fila in cursor.fetchall()]


def _fila(cursor):
    cols = _columnas(cursor)
    f = cursor.fetchone()
    return dict(zip(cols, f)) if f else None


def init_db():
    """Crea las tablas. En SQLite local, si el archivo esta corrupto o quedo
    un journal a medias (pasa al copiar la carpeta entre computadoras), lo
    rehace desde cero en vez de reventar al arrancar. En Turso no hay archivo
    local que limpiar, asi que ese rescate no aplica."""
    try:
        cx = conecta()
        try:
            for stmt in ESQUEMA:
                cx.execute(stmt)
            cx.commit()
        finally:
            cx.close()
        return
    except sqlite3.DatabaseError:
        if _es_turso():
            raise

    for f in (DB_PATH, DB_PATH + "-journal", DB_PATH + "-wal", DB_PATH + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass

    try:
        cx = conecta()
        try:
            for stmt in ESQUEMA:
                cx.execute(stmt)
            cx.commit()
        finally:
            cx.close()
    except sqlite3.DatabaseError as e:
        raise RuntimeError(
            f"No se pudo abrir la base de datos en {DB_PATH}: {e}\n\n"
            "Casi siempre es una de dos cosas:\n"
            "  1. Quedo un archivo nesting.db dañado. Borralo y vuelve a arrancar; "
            "se crea solo.\n"
            "  2. La carpeta esta en una unidad de red, en Dropbox o en Google Drive. "
            "SQLite no funciona ahi. Mueve el proyecto a una carpeta local del disco, "
            "o apunta la variable NESTING_DATA_DIR a una carpeta local."
        ) from e


def guarda(registro):
    campos = ",".join(registro.keys())
    marcas = ",".join("?" for _ in registro)
    cx = conecta()
    try:
        cx.execute(
            f"INSERT OR REPLACE INTO cotizaciones ({campos}) VALUES ({marcas})",
            list(registro.values()),
        )
        cx.commit()
    finally:
        cx.close()


def busca(texto="", limite=200):
    q = f"%{texto.strip()}%"
    cx = conecta()
    try:
        if texto.strip():
            cur = cx.execute(
                "SELECT * FROM cotizaciones WHERE cliente LIKE ? OR oneup LIKE ? "
                "OR proyecto LIKE ? ORDER BY creada DESC LIMIT ?",
                (q, q, q, limite),
            )
        else:
            cur = cx.execute(
                "SELECT * FROM cotizaciones ORDER BY creada DESC LIMIT ?", (limite,)
            )
        return _filas(cur)
    finally:
        cx.close()


def obtiene(cot_id):
    cx = conecta()
    try:
        cur = cx.execute("SELECT * FROM cotizaciones WHERE id = ?", (cot_id,))
        return _fila(cur)
    finally:
        cx.close()


def borra(cot_id):
    cx = conecta()
    try:
        cx.execute("DELETE FROM cotizaciones WHERE id = ?", (cot_id,))
        cx.commit()
    finally:
        cx.close()


def guarda_contorno(oneup, puntos):
    oneup = normaliza_1up(oneup)
    cx = conecta()
    try:
        cx.execute(
            "INSERT INTO contornos (oneup, puntos, actualizado) VALUES (?, ?, ?) "
            "ON CONFLICT(oneup) DO UPDATE SET puntos = excluded.puntos, "
            "actualizado = excluded.actualizado",
            (oneup, json.dumps(puntos), datetime.now().isoformat(timespec="seconds")),
        )
        cx.commit()
    finally:
        cx.close()


# --- Trazado ----------------------------------------------------------------

def traza(archivo, ancho_mm, alto_mm, oneup, stroke="auto"):
    """Extrae el contorno real del die-line y lo guarda en la tabla `contornos`
    (por numero de 1UP), para reusarlo despues sin volver a subir el PDF.

    Devuelve el resumen del trazado, incluido el delta contra el alto impreso:
    unos milimetros son el grosor de linea; una diferencia grande significa
    que se tomo el contorno equivocado.
    """
    salida = ruta_contorno(oneup)
    cfg = {
        "real_width_mm": float(ancho_mm),
        "real_height_mm": float(alto_mm),
        "stroke": stroke,
        "dark_threshold": 150,
        "close_iters": 4,
        "simplify_mm": 0.8,
        "out_json": salida,
    }
    cfg["pdf" if archivo.lower().endswith(".pdf") else "image"] = archivo
    resumen = _corre_script("trace_dieline.py", cfg)
    puntos = json.load(open(salida))["points"]
    guarda_contorno(oneup, puntos)
    return resumen


# --- Calculo ----------------------------------------------------------------

def calcula(puntos, cliente, oneup, proyecto, salida_html):
    """Corre el nesting sobre las cuatro bobinas estandar."""
    cfg = {
        "blank": {"points": puntos},
        "sheets": "standard",
        "gap": GAP_MM,
        "margin_top": MARGIN_TOP_MM,
        "margin_bottom": MARGIN_BOTTOM_MM,
        "margin_left": MARGIN_LEFT_MM,
        "margin_right": MARGIN_RIGHT_MM,
        "output_path": salida_html,
        "title": proyecto,
        "client": cliente,
        "oneup": normaliza_1up(oneup),
        "project": proyecto,
    }
    return _corre_script("poly_nesting.py", cfg)


def mejor_bobina(resultado):
    """Devuelve el diccionario de la bobina recomendada."""
    nombre = resultado.get("recommended_reel")
    for r in resultado.get("reels", []):
        if r.get("reel") == nombre:
            return r
    return resultado.get("reels", [{}])[0]


# --- Proceso completo -------------------------------------------------------

def corre_cotizacion(cliente, oneup, proyecto, ancho_mm, alto_mm,
                     tipo_pegue="", notas="", archivo=None, reusar=True,
                     capturo=""):
    """Traza si hace falta, calcula el nesting y guarda todo.

    Devuelve (registro, resultado_completo, resumen_trazado).
    """
    oneup = normaliza_1up(oneup)
    previo = contorno_guardado(oneup) if reusar else None
    trazado = None

    if archivo:
        trazado = traza(archivo, ancho_mm, alto_mm, oneup)
        puntos = contorno_guardado(oneup)
    elif previo:
        puntos = previo
    else:
        raise ValueError(
            f"No hay contorno guardado para {oneup} y no se subio ningun trazo. "
            "Sube el PDF o la imagen del die-line."
        )

    cot_id = uuid.uuid4().hex[:12]
    nombre = f"{oneup}_{slug(proyecto)}_{cot_id}.html"
    ruta_html = os.path.join(REPORTS_DIR, nombre)

    resultado = calcula(puntos, cliente, oneup, proyecto, ruta_html)
    mejor = mejor_bobina(resultado)
    reporte_html = open(ruta_html, encoding="utf-8").read()

    registro = {
        "id": cot_id,
        "creada": datetime.now().isoformat(timespec="seconds"),
        "cliente": cliente.strip(),
        "oneup": oneup,
        "proyecto": proyecto.strip(),
        "capturo": capturo,
        "tipo_pegue": tipo_pegue,
        "ancho_mm": float(ancho_mm),
        "alto_mm": float(alto_mm),
        "notas": notas.strip(),
        "trazo_ancho": trazado.get("width_mm") if trazado else None,
        "trazo_alto": trazado.get("height_mm") if trazado else None,
        "delta_alto": trazado.get("height_match_delta_mm") if trazado else None,
        "eficiencia": trazado.get("shape_efficiency_pct") if trazado else None,
        "bobina": mejor.get("reel"),
        "piezas": mejor.get("pieces"),
        "pliego_w": mejor.get("real_sheet_w"),
        "pliego_h": mejor.get("real_sheet_h"),
        "aprovechamiento": mejor.get("aprov_bobina_pct"),
        "merma": mejor.get("merma_bobina_pct"),
        "pzas_m2": mejor.get("pcs_per_m2"),
        "reporte": nombre,
        "reporte_html": reporte_html,
        "resultado": json.dumps(resultado),
    }

    init_db()
    guarda(registro)
    return registro, resultado, trazado


# --- Prueba desde terminal --------------------------------------------------

def autotest():
    """Corre el pipeline completo con un blank sintetico. No necesita archivos."""
    print("1. base de datos...")
    init_db()

    print("2. nesting sobre un blank de prueba...")
    puntos = [[0, 0], [300, 0], [300, 40], [340, 40], [340, 160],
              [300, 160], [300, 200], [0, 200], [0, 160], [-40, 160],
              [-40, 40], [0, 40], [0, 0]]
    html = os.path.join(REPORTS_DIR, "_autotest.html")
    res = calcula(puntos, "CLIENTE PRUEBA", "9999", "AUTOTEST", html)
    mejor = mejor_bobina(res)
    print(f"   bobina {mejor['reel']}: {mejor['pieces']} pzas, "
          f"pliego {mejor['real_sheet_w']} x {mejor['real_sheet_h']}, "
          f"aprov {mejor['aprov_bobina_pct']}%")
    assert os.path.exists(html), "no se escribio el reporte"
    assert mejor["pieces"] > 0, "no coloco ninguna pieza"

    print("3. guardado y busqueda...")
    reg = {
        "id": "test0000", "creada": datetime.now().isoformat(timespec="seconds"),
        "cliente": "CLIENTE PRUEBA", "oneup": "AF1UP9999", "proyecto": "AUTOTEST",
        "bobina": mejor["reel"], "piezas": mejor["pieces"], "reporte": "_autotest.html",
    }
    guarda(reg)
    assert any(r["id"] == "test0000" for r in busca("PRUEBA")), "no encontro el registro"
    borra("test0000")
    os.remove(html)

    print("4. normalizacion de 1UP...")
    assert normaliza_1up("1310") == "AF1UP1310"
    assert normaliza_1up("af1up1310") == "AF1UP1310"
    assert normaliza_1up(" AF1UP1310 ") == "AF1UP1310"

    print("5. contornos guardados (reemplazo de data/outlines/*.json)...")
    guarda_contorno("9999", puntos)
    recuperado = contorno_guardado("9999")
    assert recuperado == puntos, "el contorno guardado no coincide"
    cx = conecta()
    cx.execute("DELETE FROM contornos WHERE oneup = ?", ("AF1UP9999",))
    cx.commit()
    cx.close()

    print("\nTodo bien. La aplicacion esta lista para correr.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "autotest":
        autotest()
    else:
        print(__doc__)
