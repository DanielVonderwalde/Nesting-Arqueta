"""
Control de acceso.

AHORITA ESTA APAGADO: cualquiera con el link entra.

Cuando quieran login por persona, el cambio es chico y esta descrito abajo.
El resto de la aplicacion ya llama a estas funciones, asi que no hay que
tocar app.py ni core.py.

--------------------------------------------------------------------------
COMO PRENDER EL LOGIN (3 pasos)
--------------------------------------------------------------------------

1. Cambia MODO a "usuarios" en la linea de abajo.

2. Da de alta a la gente en el archivo de secretos. En local es
   .streamlit/secrets.toml; en Hugging Face Spaces se ponen en
   Settings -> Variables and secrets, con el mismo contenido:

       [usuarios]
       daniel   = "clave-de-daniel"
       marina   = "clave-de-marina"
       produccion = "clave-de-produccion"

3. Listo. Cada quien entra con su usuario y su clave, y el nombre queda
   grabado en la columna "capturo" de cada cotizacion, asi sabes quien
   corrio cada una.

Si prefieren una sola contrasena para toda la empresa en vez de usuarios
individuales, pon MODO = "clave_unica" y en secretos:

       clave_empresa = "la-clave-de-la-empresa"
"""

import streamlit as st

# "abierto" | "clave_unica" | "usuarios"
MODO = "abierto"


def _pide_clave_unica():
    esperada = st.secrets.get("clave_empresa", "")
    if not esperada:
        st.error(
            "Falta configurar `clave_empresa` en los secretos. "
            "Mientras tanto nadie puede entrar."
        )
        st.stop()

    if st.session_state.get("autenticado"):
        return "equipo"

    st.title("Nesting")
    st.caption("Acceso interno")
    with st.form("login"):
        clave = st.text_input("Contrasena", type="password")
        if st.form_submit_button("Entrar", type="primary"):
            if clave == esperada:
                st.session_state["autenticado"] = True
                st.session_state["usuario"] = "equipo"
                st.rerun()
            else:
                st.error("Contrasena incorrecta.")
    st.stop()


def _pide_usuario():
    usuarios = st.secrets.get("usuarios", {})
    if not usuarios:
        st.error(
            "No hay usuarios dados de alta en los secretos. "
            "Revisa las instrucciones en auth.py."
        )
        st.stop()

    if st.session_state.get("autenticado"):
        return st.session_state.get("usuario", "")

    st.title("Nesting")
    st.caption("Acceso interno")
    with st.form("login"):
        usuario = st.text_input("Usuario")
        clave = st.text_input("Contrasena", type="password")
        if st.form_submit_button("Entrar", type="primary"):
            if usuarios.get(usuario.strip()) == clave:
                st.session_state["autenticado"] = True
                st.session_state["usuario"] = usuario.strip()
                st.rerun()
            else:
                st.error("Usuario o contrasena incorrectos.")
    st.stop()


def usuario_actual():
    """Deja pasar al usuario y devuelve su nombre.

    Con MODO = "abierto" devuelve cadena vacia y no pide nada.
    """
    if MODO == "clave_unica":
        return _pide_clave_unica()
    if MODO == "usuarios":
        return _pide_usuario()
    return ""


def boton_salir():
    """Muestra el boton de cerrar sesion. No hace nada en modo abierto."""
    if MODO == "abierto":
        return
    if st.sidebar.button("Cerrar sesion"):
        st.session_state.clear()
        st.rerun()
