import os
import streamlit as st

# ─────────────────────────────────────────────────────────────
# IMPORTAÇÃO DE MÓDULOS INTERNOS MODULARIZADOS
# ─────────────────────────────────────────────────────────────
from database.connection import get_conn, DB_PATH
from database.schema import init_db
from database.queries import listar_produtos, listar_movimentacoes
from utils.security import inicializar_estados_sessao
from utils.drive_sync import descarregar_do_drive, FOLDER_ID

# Importações de Componentes Visuais de UI
from components.auth_ui import render_auth_ui
from components.dashboard_ui import render_dashboard_ui
from components.operations_ui import render_operations_ui
from components.audit_ui import render_audit_ui
from components.history_ui import render_history_ui
from components.ai_assistant_ui import render_ai_assistant_ui
from components.config_ui import render_config_ui

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA E CSS RESPONSIVO SEGURO
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="WMS 4.0 - Alta Performance", page_icon="📦", layout="wide")

st.markdown("""
    <style>
    /* Alinha os botões principais */
    .stButton>button {
        border-radius: 10px;
        font-weight: 600;
        height: 3em;
        width: 100%;
        margin-top: 10px;
    }
    /* Base dos cartões de métricas */
    .metric-card {
        padding: 20px;
        border-radius: 12px;
        background-color: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid rgba(128, 128, 128, 0.1);
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        margin-bottom: 15px;
    }
    .stNumberInput, .stTextInput, .stSelectbox {
        margin-bottom: 10px;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# INICIALIZAÇÃO DE BANCO E SESSOES
# ─────────────────────────────────────────────────────────────
if "db_sincronizado" not in st.session_state:
    if not os.path.exists(DB_PATH):
        descarregar_do_drive()
    init_db()
    st.session_state["db_sincronizado"] = True

# Inicia chaves de sessão na memória
inicializar_estados_sessao()

# Recuperação de sessão persistente via Token na URL
if not st.session_state["autenticado"]:
    session_token = st.query_params.get("session")
    if session_token:
        try:
            from datetime import datetime, timedelta
            with get_conn() as conn:
                sessao = conn.execute("SELECT usuario, data_criacao FROM sessoes WHERE token = ?", (session_token,)).fetchone()
            if sessao:
                usr, dt_criacao_str = sessao
                dt_criacao = datetime.strptime(dt_criacao_str, "%Y-%m-%d %H:%M:%S")
                # Sessão expira em 7 dias
                if datetime.now() - dt_criacao < timedelta(days=7):
                    with get_conn() as conn:
                        res_usr = conn.execute("SELECT aprovado, perfil FROM usuarios WHERE usuario = ?", (usr,)).fetchone()
                    if res_usr and res_usr[0] == 1:
                        st.session_state["autenticado"] = True
                        st.session_state["usuario_atual"] = usr
                        st.session_state["perfil_atual"] = res_usr[1]
                        st.rerun()
                else:
                    with get_conn() as conn:
                        conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
                    st.query_params.clear()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────
# FLUXO DE ROTEAMENTO VISUAL (AUTENTICAÇÃO & COMPONENTES)
# ─────────────────────────────────────────────────────────────
if not st.session_state["autenticado"]:
    render_auth_ui()
else:
    # Sidebar de Informações Operacionais e Logoff
    with st.sidebar:
        st.write(f"👤 Operador: **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ Nível: **{st.session_state['perfil_atual']}**")
        if st.button("🚪 Sair do Sistema (Logoff)", type="primary"):
            # Deletar sessão persistente se existir
            session_token = st.query_params.get("session")
            if session_token:
                with get_conn() as conn:
                    conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
                st.query_params.clear()
            st.session_state["autenticado"] = False
            st.session_state["usuario_atual"] = ""
            st.session_state["perfil_atual"] = ""
            st.rerun()
            
        # Leitura reativa do status de sincronia assíncrona gravado no SQLite
        with get_conn() as conn:
            status_row = conn.execute("SELECT sucesso, mensagem, timestamp FROM status_sincronismo WHERE chave = 'global'").fetchone()
        
        if status_row:
            sucesso, mensagem, timestamp_str = status_row
            if sucesso == 1:
                if "segundo plano" in mensagem:
                    st.caption(f"⏳ {mensagem}")
                else:
                    st.caption(f"🟢 {mensagem} ({timestamp_str})")
            else:
                st.error(f"⚠️ {mensagem} ({timestamp_str})")

    # Carrega DataFrames a partir das queries cacheadas
    df = listar_produtos()
    mv = listar_movimentacoes()
    
    # Abas estruturadas conforme perfil do Operador
    tabs_disponiveis = ["📊 Painel", "⚡ Saídas/Entradas", "📋 INVENTÁRIO", "📜 Histórico"]
    is_admin = st.session_state.get("perfil_atual") == "Administrador"
    
    if is_admin:
        tabs_disponiveis.extend(["🧠 IA Analista", "⚙️ Config"])
        
    abas = st.tabs(tabs_disponiveis)
    
    # Roteador visual delegando renderizações aos componentes
    with abas[0]:
        render_dashboard_ui(df)
        
    with abas[1]:
        render_operations_ui(df)
        
    with abas[2]:
        render_audit_ui(df)
        
    with abas[3]:
        render_history_ui(df, mv)
        
    if is_admin:
        with abas[4]:
            render_ai_assistant_ui(df)
        with abas[5]:
            render_config_ui(df)