import time
import streamlit as st
from datetime import datetime, timedelta
from database.connection import get_conn
from database.queries import registrar_log_auditoria

INACTIVITY_TIMEOUT = 1800  # 30 minutos em segundos

def gerenciar_timeout_sessao():
    """
    Controla o tempo de inatividade da sessão (30 minutos).
    Caso o usuário fique inativo, encerra a sessão e limpa os estados.
    """
    if st.session_state.get("autenticado"):
        agora_ts = time.time()
        ultimo_acesso = st.session_state.get("ultimo_acesso")
        
        if ultimo_acesso:
            decorrido = agora_ts - ultimo_acesso
            if decorrido > INACTIVITY_TIMEOUT:
                # Registrar auditoria do logoff automático
                registrar_log_auditoria(st.session_state["usuario_atual"], "Sessão Expirada", "Sessão encerrada por inatividade de 30 minutos.")
                
                # Deletar sessão persistente se existir na URL
                session_token = st.query_params.get("session")
                if session_token:
                    try:
                        with get_conn() as conn:
                            conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
                    except Exception:
                        pass
                    st.query_params.clear()
                    
                st.session_state["autenticado"] = False
                st.session_state["usuario_atual"] = ""
                st.session_state["perfil_atual"] = ""
                if "ultimo_acesso" in st.session_state:
                    del st.session_state["ultimo_acesso"]
                st.warning("⏱️ Sua sessão expirou devido a 30 minutos de inatividade. Faça login novamente.")
                st.rerun()
        
        # Atualiza o timestamp de atividade para a ação atual
        st.session_state["ultimo_acesso"] = agora_ts

def recuperar_sessao_persistente():
    """
    Tenta recuperar uma sessão persistente via token na URL da página.
    Validade do token: 2 horas.
    """
    if not st.session_state.get("autenticado"):
        session_token = st.query_params.get("session")
        if session_token:
            try:
                with get_conn() as conn:
                    sessao = conn.execute("SELECT usuario, data_criacao FROM sessoes WHERE token = ?", (session_token,)).fetchone()
                if sessao:
                    usr, dt_criacao_str = sessao
                    dt_criacao = datetime.strptime(dt_criacao_str, "%Y-%m-%d %H:%M:%S")
                    # Sessão expira em 2 horas
                    if datetime.now() - dt_criacao < timedelta(hours=2):
                        with get_conn() as conn:
                            res_usr = conn.execute("SELECT aprovado, perfil FROM usuarios WHERE usuario = ?", (usr,)).fetchone()
                        if res_usr and res_usr[0] == 1:
                            st.session_state["autenticado"] = True
                            st.session_state["usuario_atual"] = usr
                            st.session_state["perfil_atual"] = res_usr[1]
                            st.session_state["ultimo_acesso"] = time.time()
                            st.rerun()
                    else:
                        try:
                            with get_conn() as conn:
                                conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
                        except Exception:
                            pass
                        st.query_params.clear()
            except Exception:
                pass
