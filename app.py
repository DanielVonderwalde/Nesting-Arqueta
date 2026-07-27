"""
Nesting — aplicacion web

Para correrla en tu computadora:
    pip install -r requirements.txt
    streamlit run app.py

Se abre sola en el navegador en http://localhost:8501
"""

import json
import os
from datetime import datetime

import streamlit as st

import auth
import core

st.set_page_config(page_title="Nesting", page_icon="⬛", layout="wide")

core.init_db()
USUARIO = auth.usuario_actual()


# --- Estilo -----------------------------------------------------------------

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1200px; }
  [data-testid="stMetricValue"] { font-size: 1.6rem; }
  .ok   { color:#1a7f37; font-weight:500; }
  .aviso{ color:#9a6700; font-weight:500; }
  .mal  { color:#b42318; font-weight:500; }
</style>
""", unsafe_allow_html=True)


# --- Funciones de presentacion ---------------------------------------------

def muestra_resultado(registro, resultado, trazado=None):
    """Pinta el resumen, las alertas del trazado y el reporte completo."""
    mejor = core.mejor_bobina(resultado)

    st.subheader(f"Bobina {mejor['reel']} — {mejor['pieces']} piezas")
    c = st.columns(5)
    c[0].metric("Piezas por pliego", mejor["pieces"])
    c[1].metric("Pliego real", f"{mejor['real_sheet_w']:.0f} × {mejor['real_sheet_h']:.0f}")
    c[2].metric("Aprovechamiento", f"{mejor['aprov_bobina_pct']}%")
    c[3].metric("Merma de bobina", f"{mejor['merma_bobina_pct']}%")
    c[4].metric("Piezas por m²", mejor["pcs_per_m2"])

    st.caption(
        f"Entra en bobina de {mejor['reel_h']} mm y sobran "
        f"{mejor.get('sobra_h_mm', 0)} mm. Separacion 10 mm, pinza 20 mm, "
        "margenes 10 mm — valores fijos de planta."
    )

    # Alertas del trazado: un delta grande significa contorno equivocado.
    if trazado:
        delta = abs(trazado.get("height_match_delta_mm") or 0)
        ef = trazado.get("shape_efficiency_pct")
        st.markdown("**Verificacion del trazado**")
        col1, col2 = st.columns(2)
        col1.write(
            f"Trazado: {trazado['width_mm']} × {trazado['height_mm']} mm "
            f"contra {registro['ancho_mm']:.0f} × {registro['alto_mm']:.0f} capturados."
        )
        if delta <= 2:
            col2.markdown(
                f"<span class='ok'>Diferencia de {delta} mm — es el grosor de linea, "
                "el trazo esta bien.</span>", unsafe_allow_html=True)
        elif delta <= 5:
            col2.markdown(
                f"<span class='aviso'>Diferencia de {delta} mm — revisa la medida "
                "impresa en el dibujo antes de mandar a produccion.</span>",
                unsafe_allow_html=True)
        else:
            col2.markdown(
                f"<span class='mal'>Diferencia de {delta} mm — probablemente se tomo "
                "el contorno equivocado. No uses este resultado sin revisarlo.</span>",
                unsafe_allow_html=True)
        if ef is not None:
            st.caption(
                f"Eficiencia de forma {ef}%. "
                + ("Cerca de 100% quiere decir que el blank es casi rectangular "
                   "y el tete-beche no va a ayudar." if ef >= 95 else
                   "Hay hueco entre orejas que el tete-beche podria aprovechar.")
            )

    # Comparativa de las cuatro bobinas.
    st.markdown("**Comparativa de bobinas**")
    filas = []
    for r in resultado["reels"]:
        filas.append({
            "Bobina": f"{r['reel']} ({r['reel_h']})",
            "Mejor acomodo": r["orientation"],
            "Piezas": r["pieces"],
            "Pliego real": f"{r['real_sheet_w']:.0f} × {r['real_sheet_h']:.0f}",
            "Aprovechamiento": f"{r['aprov_bobina_pct']}%",
            "Piezas/m²": r["pcs_per_m2"],
            "Recomendada": "Si" if r["reel"] == mejor["reel"] else "",
        })
    st.dataframe(filas, use_container_width=True, hide_index=True)

    # Near-misses: acomodos que se pasan de la bobina por poco.
    casi = []
    for r in resultado["reels"]:
        for o in r.get("options", []):
            if not o.get("fits"):
                casi.append(
                    f"{r['reel']} / {o['orientation']}"
                    f"{' tete-beche' if o.get('tete_beche') else ''}: "
                    f"{o['pieces']} piezas pero se pasa "
                    f"{max(o['over_w_mm'], o['over_h_mm']):.1f} mm"
                )
    if casi:
        with st.expander(f"Acomodos que se pasan por poco ({len(casi)})"):
            st.write(
                "Estos NO caben con los margenes de planta. Se muestran porque "
                "unos milimetros a veces se arreglan en el suaje, y esa es una "
                "decision tuya, no mia. No tomes su aprovechamiento como real."
            )
            for x in casi:
                st.write("- " + x)

    # Reporte completo interactivo. Vive en la base (columna reporte_html),
    # no en disco, para que sobreviva a un reinicio en hosting gratis.
    html = registro.get("reporte_html")
    if html:
        st.markdown("**Reporte completo**")
        st.components.v1.html(html, height=780, scrolling=True)
        st.download_button(
            "Descargar el reporte", html, file_name=registro["reporte"],
            mime="text/html",
        )

    enlace = f"?id={registro['id']}"
    st.info(
        f"Guardado. Para volver a esta cotizacion agrega **{enlace}** al final "
        "de la direccion, o buscala en Historial por cliente o numero de 1UP."
    )


# --- Vista: nueva cotizacion ------------------------------------------------

def vista_nueva():
    st.title("Nueva cotizacion")
    st.caption(
        "Llena los datos del trazo y calculo el acomodo en las cuatro bobinas."
    )

    with st.form("datos"):
        c1, c2 = st.columns(2)
        cliente = c1.text_input("Cliente", placeholder="INDUSTRIAS ARCOIRIS")
        proyecto = c2.text_input("Proyecto", placeholder="MARINA_AZUL_8_PACK")

        c3, c4, c5 = st.columns(3)
        oneup = c3.text_input("Numero de 1UP", placeholder="1310 o AF1UP1310")
        ancho = c4.number_input("Ancho del 1up (mm)", min_value=1.0, value=396.0, step=0.5)
        alto = c5.number_input("Alto del 1up (mm)", min_value=1.0, value=280.0, step=0.5)
        c5.caption("El blank completo, no el panel del cuerpo.")

        pegue = st.radio("Tipo de pegue", core.TIPOS_PEGUE, horizontal=True)
        pegue_otro = st.text_input(
            "Si es otro, describelo", placeholder="solo si elegiste Otro")

        archivo = st.file_uploader(
            "Trazo del die-line (PDF, PNG o JPG)", type=["pdf", "png", "jpg", "jpeg"])
        notas = st.text_area(
            "Notas", placeholder="Material, calibre, medidas de paneles, lo que sea util despues")

        enviar = st.form_submit_button("Calcular", type="primary")

    if not enviar:
        # Aviso util: si el 1UP ya se trazo antes, no hace falta subir nada.
        st.caption(
            "Si el 1UP ya se corrio antes, no necesitas volver a subir el trazo: "
            "se reutiliza el contorno guardado."
        )
        return

    faltan = [n for n, v in
              [("Cliente", cliente), ("Proyecto", proyecto), ("1UP", oneup)] if not v.strip()]
    if faltan:
        st.error("Falta llenar: " + ", ".join(faltan))
        return

    oneup_n = core.normaliza_1up(oneup)
    previo = core.contorno_guardado(oneup_n)

    if not archivo and not previo:
        st.error(
            f"No hay contorno guardado para {oneup_n} y no subiste el trazo. "
            "Sube el PDF o la imagen del die-line."
        )
        return

    ruta_subida = None
    if archivo:
        ruta_subida = os.path.join(
            core.UPLOADS_DIR,
            f"{oneup_n}_{datetime.now():%Y%m%d%H%M%S}_{archivo.name}")
        with open(ruta_subida, "wb") as f:
            f.write(archivo.getbuffer())
    elif previo:
        st.info(f"Reutilizando el contorno que ya teniamos de {oneup_n}.")

    tipo = pegue_otro.strip() if pegue == "Otro" and pegue_otro.strip() else pegue

    with st.spinner("Trazando el contorno y probando acomodos..."):
        try:
            registro, resultado, trazado = core.corre_cotizacion(
                cliente=cliente, oneup=oneup_n, proyecto=proyecto,
                ancho_mm=ancho, alto_mm=alto, tipo_pegue=tipo, notas=notas,
                archivo=ruta_subida, capturo=USUARIO,
            )
        except Exception as e:
            st.error(f"No se pudo calcular: {e}")
            return

    muestra_resultado(registro, resultado, trazado)


# --- Vista: historial -------------------------------------------------------

def vista_historial():
    st.title("Historial")
    st.caption("Todas las cotizaciones corridas. Busca por cliente, 1UP o proyecto.")

    q = st.text_input("Buscar", placeholder="Marina Azul, 1310, plastilina...")
    filas = core.busca(q)

    if not filas:
        st.info("No hay cotizaciones todavia." if not q
                else f"Nada encontrado para «{q}».")
        return

    tabla = [{
        "Fecha": r["creada"][:16].replace("T", " "),
        "Cliente": r["cliente"],
        "1UP": r["oneup"],
        "Proyecto": r["proyecto"],
        "Bobina": r["bobina"],
        "Piezas": r["piezas"],
        "Aprov.": f"{r['aprovechamiento']}%" if r["aprovechamiento"] else "",
        "Abrir": f"?id={r['id']}",
    } for r in filas]
    st.dataframe(tabla, use_container_width=True, hide_index=True)

    st.download_button(
        "Descargar el historial en CSV",
        _csv(filas), file_name="historial-nesting.csv", mime="text/csv",
    )

    etiquetas = {
        f"{r['creada'][:10]} · {r['cliente']} · {r['oneup']} · {r['proyecto']}": r["id"]
        for r in filas
    }
    elegida = st.selectbox("Abrir una cotizacion", list(etiquetas))
    if st.button("Ver", type="primary"):
        st.query_params["id"] = etiquetas[elegida]
        st.rerun()


def _csv(filas):
    import csv, io
    cols = ["creada", "cliente", "oneup", "proyecto", "tipo_pegue", "capturo",
            "ancho_mm", "alto_mm", "bobina", "piezas", "pliego_w", "pliego_h",
            "aprovechamiento", "merma", "pzas_m2", "notas"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in filas:
        w.writerow(r)
    return buf.getvalue()


# --- Vista: una cotizacion guardada ----------------------------------------

def vista_guardada(cot_id):
    registro = core.obtiene(cot_id)
    if not registro:
        st.error("Esa cotizacion ya no existe.")
        if st.button("Volver"):
            st.query_params.clear()
            st.rerun()
        return

    st.title(registro["proyecto"])
    st.caption(
        f"{registro['oneup']} · {registro['cliente']} · "
        f"{registro['creada'][:16].replace('T', ' ')}"
        + (f" · capturo {registro['capturo']}" if registro.get("capturo") else "")
    )
    if registro.get("tipo_pegue"):
        st.caption(f"Pegue: {registro['tipo_pegue']}")
    if registro.get("notas"):
        st.info(registro["notas"])

    trazado = None
    if registro.get("trazo_ancho"):
        trazado = {
            "width_mm": registro["trazo_ancho"],
            "height_mm": registro["trazo_alto"],
            "height_match_delta_mm": registro["delta_alto"],
            "shape_efficiency_pct": registro["eficiencia"],
        }

    muestra_resultado(registro, json.loads(registro["resultado"]), trazado)

    if st.button("Volver al inicio"):
        st.query_params.clear()
        st.rerun()


# --- Enrutador --------------------------------------------------------------

st.sidebar.title("Nesting")
st.sidebar.caption("Acomodo de suajes en pliego")

cot_id = st.query_params.get("id")

if cot_id:
    if st.sidebar.button("Nueva cotizacion"):
        st.query_params.clear()
        st.rerun()
    vista_guardada(cot_id)
else:
    vista = st.sidebar.radio("", ["Nueva cotizacion", "Historial"], label_visibility="collapsed")
    if vista == "Nueva cotizacion":
        vista_nueva()
    else:
        vista_historial()

with st.sidebar:
    st.divider()
    st.caption(
        "**Estandares fijos de planta**\n\n"
        "Separacion entre cajas 10 mm · Pinza 20 mm · "
        "Margen inferior 10 mm · Margenes laterales 10 mm\n\n"
        "No se ajustan para ganar piezas."
    )
    st.caption(
        "**Bobinas**\n\n"
        "Grande 710 · Mediano 610 · Chico 450 · Extra chico 355 mm\n\n"
        "El ancho se recorta libre hasta 1040 mm."
    )
    if USUARIO:
        st.caption(f"Sesion de {USUARIO}")
    auth.boton_salir()
