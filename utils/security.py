import hashlib
import streamlit as st

def gerar_hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

def inicializar_estados_sessao():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False
        st.session_state["usuario_atual"] = ""
        st.session_state["perfil_atual"] = ""
