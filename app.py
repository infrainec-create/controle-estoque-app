import os
import streamlit as st

# ─────────────────────────────────────────────────────────────
# IMPORTAÇÃO DE MÓDULOS INTERNOS MODULARIZADOS
# ─────────────────────────────────────────────────────────────
from database.connection import get_conn, DB_PATH
from database.schema import init_db
from database.queries import listar_produtos, listar_movimentacoes, registrar_log_auditoria
from utils.security import inicializar_estados_sessao
from utils.drive_sync import descarregar_do_drive, FOLDER_ID, sincronizar_banco_na_inicializacao

# Importações de Componentes Visuais de UI
from components.auth_ui import render_auth_ui
from components.dashboard_ui import render_dashboard_ui
from components.operations_ui import render_operations_ui
from components.audit_ui import render_audit_ui
from components.history_ui import render_history_ui
from components.ai_assistant_ui import render_ai_assistant_ui
from components.config_ui import render_config_ui
from components.schedule_ui import render_schedule_ui

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA E CSS RESPONSIVO SEGURO
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="WMS 5.0", page_icon="📦", layout="wide")

st.markdown("""
    <style>
    /* Alinha os botões principais */
    .stButton>button {
        border-radius: 12px;
        font-weight: 600;
        height: 3.2em;
        width: 100%;
        margin-top: 10px;
        transition: all 0.2s ease-in-out;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
    }
    /* Base dos cartões de métricas Premium */
    .metric-card {
        padding: 22px;
        border-radius: 16px;
        background: linear-gradient(135deg, var(--secondary-background-color) 0%, rgba(128, 128, 128, 0.03) 100%);
        color: var(--text-color);
        border: 1px solid rgba(128, 128, 128, 0.12);
        box-shadow: 0 8px 16px rgba(0, 0, 0, 0.03);
        margin-bottom: 15px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 12px 24px rgba(0, 0, 0, 0.08);
        border-color: rgba(128, 128, 128, 0.25);
    }
    /* Brilho sutil no canto superior-esquerdo */
    .metric-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0) 50%);
        pointer-events: none;
    }
    /* Elementos internos do card */
    .metric-card .card-title {
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text-color);
        opacity: 0.75;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .metric-card .card-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: var(--text-color);
        line-height: 1.1;
    }
    .stNumberInput, .stTextInput, .stSelectbox {
        margin-bottom: 10px;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# INICIALIZAÇÃO DE BANCO E SESSOES COM RESILIÊNCIA CRÍTICA
# ─────────────────────────────────────────────────────────────
if "db_sincronizado" not in st.session_state:
    try:
        # Tenta aplicar FOLDER_ID e credenciais customizadas se presentes na sessão antes de sincronizar
        if st.session_state.get("folder_id_custom"):
            import utils.drive_sync as ds
            ds.FOLDER_ID = st.session_state["folder_id_custom"]
        sincronizar_banco_na_inicializacao()
        init_db()
        st.session_state["db_sincronizado"] = True
    except Exception as e:
        # Exibe uma tela de bloqueio premium caso não haja banco local e a sincronização com o Drive falhe
        st.markdown(f"""
            <div style="text-align: center; padding: 40px; background-color: rgba(255, 75, 75, 0.05); border-radius: 15px; border: 1px solid rgba(255, 75, 75, 0.3); margin-top: 50px;">
                <h1 style="color: #FF4B4B; font-size: 2.5rem; margin-bottom: 20px;">☁️ Conexão com a Nuvem Indisponível</h1>
                <p style="font-size: 1.2rem; margin-bottom: 20px; line-height: 1.6;">
                    Não foi possível encontrar um banco de dados local e o download inicial do Google Drive falhou.
                </p>
                <div style="background-color: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 10px; border-left: 5px solid #FF4B4B; text-align: left; margin: 0 auto; max-width: 600px; font-family: monospace; font-size: 0.95rem;">
                    <strong>Erro Técnico:</strong><br>
                    {str(e)}
                </div>
                <p style="margin-top: 25px; font-size: 1.05rem;">
                    Para evitar a criação de um banco de dados em branco (o que poderia sobrescrever seu estoque real), o sistema foi bloqueado por segurança.
                </p>
                <p style="font-size: 1rem; color: gray; margin-top: 10px;">
                    Por favor, verifique suas credenciais de nuvem em <code>secrets.toml</code>, o ID da pasta ou escolha uma das opções abaixo para prosseguir.
                </p>
            </div>
        """, unsafe_allow_html=True)
        
        st.write("") # Espaçador
        c_opt1, c_opt2 = st.columns(2)
        
        with c_opt1:
            st.markdown("### 📴 Modo Offline")
            st.write("Trabalhe localmente com um banco de dados temporário. A sincronização com a nuvem ficará desativada.")
            if st.button("🔌 Usar Banco Local Temporário", use_container_width=True):
                try:
                    init_db()
                    st.session_state["db_sincronizado"] = "local"
                    st.toast("Modo Offline ativado!", icon="🔌")
                    st.rerun()
                except Exception as ex_db:
                    st.error(f"Erro ao inicializar o banco local: {ex_db}")
                    
        with c_opt2:
            st.markdown("### 🔑 Corrigir Credenciais")
            st.write("Configure as chaves e o ID da pasta do Google Drive em memória para testar a sincronização imediatamente.")
            
            with st.popover("⚙️ Configurar Credenciais em Memória", use_container_width=True):
                st.subheader("Configurações do Google Drive")
                new_folder_id = st.text_input("Folder ID do Google Drive:", value=st.session_state.get("folder_id_custom", ""))
                
                uploaded_json = st.file_uploader("Carregar JSON de Credenciais da Conta de Serviço (GCP):", type=["json"])
                
                if st.button("💾 Aplicar e Tentar Conexão", type="primary", use_container_width=True):
                    if new_folder_id:
                        st.session_state["folder_id_custom"] = new_folder_id
                        import utils.drive_sync as ds
                        ds.FOLDER_ID = new_folder_id
                        
                    if uploaded_json is not None:
                        try:
                            import json
                            creds_data = json.load(uploaded_json)
                            required_keys = ["type", "project_id", "private_key", "client_email"]
                            if all(k in creds_data for k in required_keys):
                                st.session_state["gcp_service_account_custom"] = creds_data
                                st.success("Credenciais do GCP carregadas em memória com sucesso!")
                            else:
                                st.error("O arquivo JSON não possui os campos obrigatórios de uma Conta de Serviço Google Cloud.")
                        except Exception as e_json:
                            st.error(f"Erro ao ler arquivo JSON: {e_json}")
                            
                    st.rerun()
        
        st.write("---")
        col1, col2, col3 = st.columns([2, 1, 2])
        with col2:
            if st.button("🔄 Tentar Novamente", type="primary", use_container_width=True):
                st.rerun()
        st.stop()

# Inicia chaves de sessão na memória
inicializar_estados_sessao()

# --- CONTROLE DE EXPIRAÇÃO DE SESSÃO POR INATIVIDADE (30 MINUTOS) ---
import time
INACTIVITY_TIMEOUT = 1800  # 30 minutos em segundos

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
                with get_conn() as conn:
                    conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
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

# Recuperação de sessão persistente via Token na URL (Tempo reduzido para 2 horas para maior segurança)
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
    # Limpeza de segurança para evitar vazamento do token de sessão na URL/barra do navegador
    if "session" in st.query_params:
        try:
            st.query_params.clear()
        except Exception:
            pass

    # Alerta Proeminente no Topo se a Sincronização falhar ou tiver Conflito
    if st.session_state.get("db_sincronizado") == "local":
        st.warning("🔌 **Modo Offline Ativo:** A sincronização com o Google Drive está desativada. As alterações serão salvas localmente apenas.")
    else:
        with get_conn() as conn:
            status_row_main = conn.execute("SELECT sucesso, mensagem, timestamp FROM status_sincronismo WHERE chave = 'global'").fetchone()
        if status_row_main and status_row_main[0] == 0:
            st.error(f"⚠️ **Alerta de Sincronização:** {status_row_main[1]} (Registrado em: {status_row_main[2]})")

    # Sidebar de Informações Operacionais e Logoff
    with st.sidebar:
        st.write(f"👤 Operador: **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ Nível: **{st.session_state['perfil_atual']}**")
        if st.button("🚪 Sair do Sistema (Logoff)", type="primary"):
            # Registrar log de auditoria antes de limpar a sessão
            registrar_log_auditoria(st.session_state["usuario_atual"], "Logoff no Sistema", "Operador encerrou a sessão manualmente.")
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
        if st.session_state.get("db_sincronizado") == "local":
            st.caption("🟡 Sincronização Desativada (Modo Offline)")
        else:
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
    tabs_disponiveis = ["📊 Painel", "⚡ Saídas/Entradas", "📋 INVENTÁRIO", "📜 Histórico", "📅 Cronograma"]
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
        
    with abas[4]:
        render_schedule_ui(df)
        
    if is_admin:
        with abas[5]:
            render_ai_assistant_ui(df)
        with abas[6]:
            render_config_ui(df)