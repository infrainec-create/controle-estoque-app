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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    
    <style>
    /* ─────────────────────────────────────────────────────────────
       GLOBAL STYLING OVERRIDES (FONTS & UTILITIES)
       ───────────────────────────────────────────────────────────── */
    /* Cascades font-family to layout containers while preserving SVG and font-based icon families */
    html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stSidebar"], .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .stMarkdown, p, h1, h2, h3, h4, h5, h6, label, input, select, textarea {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    
    /* ─────────────────────────────────────────────────────────────
       PREMIUM METRIC CARDS
       ───────────────────────────────────────────────────────────── */
    .metric-card {
        padding: 24px;
        border-radius: 20px;
        background: linear-gradient(135deg, var(--secondary-background-color) 0%, rgba(0, 114, 255, 0.02) 100%);
        color: var(--text-color);
        border: 1px solid rgba(128, 128, 128, 0.12);
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.02);
        margin-bottom: 20px;
        transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
        position: relative;
        overflow: hidden;
    }
    
    .metric-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 16px 35px rgba(0, 114, 255, 0.08);
        border-color: rgba(0, 114, 255, 0.35);
    }
    
    .metric-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0) 60%);
        pointer-events: none;
    }
    
    .metric-card .card-title {
        font-size: 0.8rem;
        font-weight: 700;
        color: var(--text-color);
        opacity: 0.65;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    
    .metric-card .card-value {
        font-size: 2rem;
        font-weight: 800;
        color: var(--text-color) !important;
        line-height: 1.1;
    }

    /* ─────────────────────────────────────────────────────────────
       NAV PILLS (TABS RE-DESIGN)
       ───────────────────────────────────────────────────────────── */
    /* Clean up so they do not squeeze when there are multiple tabs */
    div[data-baseweb="tab-list"] {
        background-color: var(--secondary-background-color) !important;
        border-radius: 14px !important;
        padding: 6px !important;
        border: 1px solid rgba(128, 128, 128, 0.08) !important;
        margin-bottom: 25px !important;
    }
    
    div[data-baseweb="tab-list"] button {
        border-radius: 10px !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        color: var(--text-color) !important;
        opacity: 0.7;
        border: none !important;
        background: transparent !important;
        transition: all 0.25s ease-in-out !important;
        padding: 10px 16px !important;
    }
    
    div[data-baseweb="tab-list"] button:hover {
        opacity: 1 !important;
        color: var(--primary-color) !important;
    }
    
    div[data-baseweb="tab-list"] button[aria-selected="true"] {
        background-color: var(--background-color) !important;
        color: var(--primary-color) !important;
        opacity: 1 !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05) !important;
    }
    
    div[data-baseweb="tab-highlight"] {
        display: none !important;
    }

    /* ─────────────────────────────────────────────────────────────
       INPUT FIELDS (TEXT, NUMBER, SELECTBOX, TEXTAREA)
       ───────────────────────────────────────────────────────────── */
    div[data-testid="stTextInput"] input, 
    div[data-testid="stNumberInput"] input, 
    div[data-testid="stSelectbox"] select, 
    div[data-testid="stTextArea"] textarea {
        border-radius: 10px !important;
        border: 1px solid rgba(128, 128, 128, 0.18) !important;
        background-color: var(--background-color) !important;
        color: var(--text-color) !important;
        padding: 10px 14px !important;
        font-size: 0.92rem !important;
        transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
    }
    
    div[data-testid="stTextInput"] input:focus, 
    div[data-testid="stNumberInput"] input:focus, 
    div[data-testid="stSelectbox"] select:focus, 
    div[data-testid="stTextArea"] textarea:focus {
        border-color: var(--primary-color) !important;
        box-shadow: 0 0 0 3px rgba(0, 114, 255, 0.12) !important;
        outline: none !important;
    }

    /* ─────────────────────────────────────────────────────────────
       BUTTON STYLING (GLOBAL SENSE)
       ───────────────────────────────────────────────────────────── */
    div.stButton > button, button[type="submit"] {
        border-radius: 12px !important;
        font-weight: 700 !important;
        transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
        border: 1px solid rgba(128, 128, 128, 0.15) !important;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.02) !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    div.stButton > button:hover, button[type="submit"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.08) !important;
        border-color: var(--primary-color) !important;
    }
    
    /* Primary buttons (blue gradient) */
    div.stButton > button[kind="primary"], button[type="submit"] {
        background: linear-gradient(135deg, #0072FF 0%, #00C6FF 100%) !important;
        color: white !important;
        border: none !important;
        box-shadow: 0 4px 15px rgba(0, 114, 255, 0.2) !important;
    }
    
    div.stButton > button[kind="primary"]:hover, button[type="submit"]:hover {
        background: linear-gradient(135deg, #0056b3 0%, #0072FF 100%) !important;
        box-shadow: 0 8px 25px rgba(0, 114, 255, 0.3) !important;
    }

    /* ─────────────────────────────────────────────────────────────
       CONTAINERS WITH BORDER (BORDERED CARD-LIKE BLOCKS)
       ───────────────────────────────────────────────────────────── */
    /* Remove padding override to allow Streamlit's inner engine to handle padding layout correctly */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: var(--secondary-background-color) !important;
        border: 1px solid rgba(128, 128, 128, 0.12) !important;
        border-radius: 20px !important;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.02) !important;
        margin-bottom: 15px !important;
    }

    /* Hide MainMenu & Footer */
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

# Defaults do fluxo e controles reativos da UI
if "metodo_consumo" not in st.session_state:
    st.session_state["metodo_consumo"] = "movimentacoes"
if "login_attempts" not in st.session_state:
    st.session_state["login_attempts"] = 0
if "recovery_attempts" not in st.session_state:
    st.session_state["recovery_attempts"] = 0

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
            
        # Cronômetro de Sessão regressivo em tempo real
        import streamlit.components.v1 as components
        timer_html = f"<!-- timestamp: {int(st.session_state['ultimo_acesso'])} -->\n" + """
        <div style="
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            padding: 10px 14px;
            background-color: rgba(255, 75, 75, 0.08);
            border-radius: 10px;
            border: 1px solid rgba(255, 75, 75, 0.2);
            text-align: center;
            margin-top: 5px;
            margin-bottom: 10px;
        ">
            <span style="font-size: 0.72rem; color: #888; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; display: block; margin-bottom: 2px;">
                ⏳ Tempo de Sessão Ativa
            </span>
            <div id="countdown" style="font-size: 1.55rem; font-weight: 700; color: #FF4B4B; font-variant-numeric: tabular-nums;">
                30:00
            </div>
            <span style="font-size: 0.65rem; color: #777; display: block; margin-top: 2px;">
                Reseta automaticamente ao interagir
            </span>
        </div>
        <script>
            var duration = 1800; // 30 minutos em segundos
            var timer = duration;
            var display = document.getElementById('countdown');
            
            var countdownInterval = setInterval(function () {
                var minutes = parseInt(timer / 60, 10);
                var seconds = parseInt(timer % 60, 10);

                minutes = minutes < 10 ? "0" + minutes : minutes;
                seconds = seconds < 10 ? "0" + seconds : seconds;

                display.textContent = minutes + ":" + seconds;

                if (--timer < 0) {
                    clearInterval(countdownInterval);
                    try {
                        window.parent.location.reload();
                    } catch (e) {
                        window.location.reload();
                    }
                }
            }, 1000);
        </script>
        """
        components.html(timer_html, height=105)
            
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
    if not df.empty and "criticidade" not in df.columns:
        df["criticidade"] = "Y"
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