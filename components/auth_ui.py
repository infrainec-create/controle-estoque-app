import uuid
from datetime import datetime
import streamlit as st
from database.connection import get_conn
from utils.security import gerar_hash_senha, normalizar_usuario
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria

def render_auth_ui():
    # Injetar fontes modernas (Inter) e estilos CSS responsivos de altíssima qualidade
    st.markdown("""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        
        <style>
        /* Tipografia Global do Painel de Login */
        .stApp, div[data-testid="stForm"], div[data-baseweb="tab-list"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }
        input, select, p, h1, h2, h3 {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }

        /* Container do Formulário de Acesso (Card Glassmorphism) */
        div[data-testid="stForm"] {
            background-color: var(--secondary-background-color) !important;
            border: 1px solid rgba(128, 128, 128, 0.12) !important;
            border-radius: 24px !important;
            padding: 40px !important;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.06) !important;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1) !important;
            margin-top: 10px;
        }
        
        div[data-testid="stForm"]:hover {
            border-color: rgba(0, 114, 255, 0.35) !important;
            box-shadow: 0 30px 60px rgba(0, 114, 255, 0.08) !important;
            transform: translateY(-2px);
        }

        /* Menu de Navegação por Abas (Tab Navigation) */
        div[data-baseweb="tab-list"] {
            background-color: rgba(128, 128, 128, 0.05) !important;
            border-radius: 14px !important;
            padding: 6px !important;
            border: 1px solid rgba(128, 128, 128, 0.08) !important;
            margin-bottom: 25px !important;
            display: flex !important;
            justify-content: space-around !important;
        }
        
        div[data-baseweb="tab-list"] button {
            border-radius: 10px !important;
            font-weight: 600 !important;
            font-size: 0.92rem !important;
            color: var(--text-color) !important;
            opacity: 0.7;
            border: none !important;
            background: transparent !important;
            transition: all 0.25s ease-in-out !important;
            flex-grow: 1 !important;
            text-align: center !important;
            padding: 10px 14px !important;
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
        
        /* Oculta a barra vermelha padrão de seleção de abas do Streamlit */
        div[data-baseweb="tab-highlight"] {
            display: none !important;
        }

        /* Estilização dos Campos de Entrada (Text Inputs & Selects) */
        div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] select {
            border-radius: 12px !important;
            border: 1px solid rgba(128, 128, 128, 0.18) !important;
            background-color: var(--background-color) !important;
            color: var(--text-color) !important;
            padding: 12px 16px !important;
            font-size: 0.95rem !important;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
        }
        
        div[data-testid="stTextInput"] input:focus, div[data-testid="stSelectbox"] select:focus {
            border-color: var(--primary-color) !important;
            box-shadow: 0 0 0 4px rgba(0, 114, 255, 0.15) !important;
            outline: none !important;
        }

        /* Botões de Envio e Execução */
        div.stButton > button, div[data-testid="stForm"] button[type="submit"] {
            background: linear-gradient(135deg, #0072FF 0%, #00C6FF 100%) !important;
            color: white !important;
            border: none !important;
            border-radius: 12px !important;
            padding: 14px 24px !important;
            font-size: 1rem !important;
            font-weight: 700 !important;
            cursor: pointer !important;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
            box-shadow: 0 4px 15px rgba(0, 114, 255, 0.2) !important;
            margin-top: 15px !important;
            width: 100% !important;
            height: auto !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        div.stButton > button:hover, div[data-testid="stForm"] button[type="submit"]:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 8px 25px rgba(0, 114, 255, 0.3) !important;
            background: linear-gradient(135deg, #0056b3 0%, #0072FF 100%) !important;
        }
        
        div.stButton > button:active, div[data-testid="stForm"] button[type="submit"]:active {
            transform: translateY(0) !important;
            box-shadow: 0 4px 10px rgba(0, 114, 255, 0.15) !important;
        }
        
        /* Ajuste fino dos rótulos (labels) */
        label {
            font-weight: 600 !important;
            font-size: 0.88rem !important;
            color: var(--text-color) !important;
            opacity: 0.95;
            margin-bottom: 6px !important;
        }

        /* Card informativo e alertas elegantes */
        .stAlert {
            border-radius: 14px !important;
            border: 1px solid rgba(128, 128, 128, 0.08) !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # Layout centralizado responsivo
    col_left, col_center, col_right = st.columns([0.5, 2.5, 0.5])
    
    with col_center:
        # Header Premium com Branding Minimalista e Gradiente dinâmico
        st.markdown("""
            <div style="text-align: center; margin-top: 35px; margin-bottom: 30px;">
                <div style="
                    display: inline-block; 
                    background: linear-gradient(135deg, #0072FF 0%, #00C6FF 100%);
                    padding: 20px; 
                    border-radius: 24px; 
                    box-shadow: 0 12px 30px rgba(0, 114, 255, 0.25);
                    margin-bottom: 20px;
                    transform: rotate(-3deg);
                ">
                    <span style="font-size: 3.2rem; line-height: 1; display: block;">📦</span>
                </div>
                <h1 style="
                    font-weight: 800; 
                    font-size: 2.4rem; 
                    background: linear-gradient(135deg, #0072FF, #00C6FF); 
                    -webkit-background-clip: text; 
                    -webkit-text-fill-color: transparent; 
                    margin: 0;
                    letter-spacing: -0.8px;
                ">WMS INTELIGENTE</h1>
                <p style="
                    color: var(--text-color); 
                    opacity: 0.65; 
                    font-size: 1.05rem; 
                    font-weight: 500; 
                    margin-top: 6px;
                    margin-bottom: 0;
                ">Controle de Estoque e Auditoria Avançada</p>
            </div>
        """, unsafe_allow_html=True)

        st.session_state.setdefault("login_attempts", 0)
        st.session_state.setdefault("recovery_attempts", 0)
        max_attempts = 5

        aba_login, aba_cadastro, aba_recuperar = st.tabs(["🔑 Entrar no Sistema", "👤 Criar Conta", "🛠️ Esqueci a Senha"])
        
        with aba_login:
            with st.form("form_login"):
                usr_input = st.text_input("Usuário (Login)").strip()
                pass_input = st.text_input("Senha", type="password")
                btn_login = st.form_submit_button("Acessar WMS")
                
                if btn_login:
                    if st.session_state["login_attempts"] >= max_attempts:
                        st.error("Você excedeu o número máximo de tentativas de login. Tente novamente mais tarde.")
                    elif usr_input and pass_input:
                        usr_input_norm = normalizar_usuario(usr_input)
                        try:
                            with get_conn() as conn:
                                res = conn.execute("SELECT aprovado, perfil, senha_hash, usuario FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usr_input_norm,)).fetchone()
                        except Exception as db_err:
                            st.error(f"❌ Erro ao acessar banco de dados: {db_err}")
                            res = None
                        
                        from utils.security import verificar_e_atualizar_senha
                        if res and verificar_e_atualizar_senha(res[3], pass_input, res[2]):
                            aprovado, perfil, _, db_usr = res
                            st.session_state["login_attempts"] = 0
                            if aprovado == 1:
                                session_token = str(uuid.uuid4())
                                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                session_saved = False
                                try:
                                    with get_conn() as conn:
                                        conn.execute("INSERT OR REPLACE INTO sessoes (token, usuario, data_criacao) VALUES (?, ?, ?)", (session_token, db_usr, now_str))
                                    session_saved = True
                                except Exception as db_err:
                                    st.warning("⚠️ Não foi possível salvar a sessão persistente no banco de dados. O login continuará em modo temporário.")
                                    print(f"Erro ao salvar sessao persistente: {db_err}")
                                
                                if session_saved:
                                    st.query_params["session"] = session_token

                                st.session_state["autenticado"] = True
                                st.session_state["usuario_atual"] = db_usr
                                st.session_state["perfil_atual"] = perfil
                                
                                registrar_log_auditoria(db_usr, "Login no Sistema", f"Operador realizou login com sucesso. Perfil: {perfil}.")
                                
                                st.toast(f"Bem-vindo de volta, {db_usr}!", icon="👋")
                                st.rerun()
                            elif aprovado == 2:
                                st.error("🚫 Sua conta está suspensa temporariamente. Entre em contato com o administrador.")
                            else:
                                st.error("⏳ Seu cadastro está pendente de aprovação. Solicite ao administrador a liberação do seu acesso.")
                        else:
                            st.session_state["login_attempts"] += 1
                            tentativas_restantes = max_attempts - st.session_state["login_attempts"]
                            st.error(f"❌ Usuário ou senha incorretos. Tente novamente. Restam {tentativas_restantes} tentativas.")
                    else:
                        st.warning("Preencha todos os campos para fazer o login.")
                        
        with aba_cadastro:
            st.subheader("📝 Solicitar Novo Acesso")
            st.info("Nota: Após o envio, seu cadastro ficará na fila de aprovação do administrador do sistema.")
            with st.form("form_cadastro"):
                new_usr = st.text_input("Escolha um Nome de Usuário").strip()
                new_pass = st.text_input("Escolha uma Senha (mínimo 8 caracteres, letras e números)", type="password")
                pergunta = st.selectbox("Escolha uma pergunta de segurança para recuperação:", [
                    "Qual o nome do seu primeiro animal de estimação?",
                    "Qual a sua cidade natal?",
                    "Qual o nome da sua mãe?",
                    "Qual o nome do seu primeiro colégio?"
                ])
                resposta = st.text_input("Resposta da Pergunta de Segurança").strip().lower()
                btn_cadastrar = st.form_submit_button("Enviar Solicitação de Cadastro")
                
                if btn_cadastrar:
                    if new_usr and new_pass and resposta:
                        import re
                        if len(new_pass) < 8 or not re.search(r"[a-zA-Z]", new_pass) or not re.search(r"\d", new_pass):
                            st.error("❌ A senha deve conter pelo menos 8 caracteres, incluindo pelo menos uma letra e um número.")
                        else:
                            cleaned_username = normalizar_usuario(new_usr)
                            normalized_response = resposta.lower().strip()
                            try:
                                with get_conn() as conn:
                                    exists = conn.execute("SELECT 1 FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (cleaned_username,)).fetchone()
                                    
                                    if exists:
                                        st.error("❌ Esse nome de usuário já existe na base. Tente uma combinação diferente.")
                                    else:
                                        total_usuarios = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
                                        if total_usuarios == 0:
                                            status_inicial = 1
                                            perfil_inicial = "Administrador"
                                        else:
                                            status_inicial = 0
                                            perfil_inicial = "Operador"
                                             
                                        conn.execute(
                                            "INSERT INTO usuarios (usuario, senha_hash, pergunta_seguranca, resposta_seguranca_hash, aprovado, perfil) VALUES (?, ?, ?, ?, ?, ?)",
                                            (cleaned_username, gerar_hash_senha(new_pass), pergunta, gerar_hash_senha(normalized_response), status_inicial, perfil_inicial)
                                        )
                                         
                                        registrar_log_auditoria(cleaned_username if status_inicial == 1 else "Sistema", "Solicitação de Cadastro", f"Solicitação de cadastro enviada para o usuário '{cleaned_username}' (Perfil inicial: {perfil_inicial}, Aprovado: {'Sim' if status_inicial == 1 else 'Não'}).")
                                         
                                        disparar_sincronizacao()
                                        if status_inicial == 1:
                                            st.success("👑 Conta de administrador master criada! Vá para a aba Entrar e realize o login.")
                                        else:
                                            st.success(f"⏳ Solicitação enviada! O usuário '{cleaned_username}' foi colocado na fila de aceitação do Administrador.")
                            except Exception as e:
                                st.error(f"❌ Ocorreu um erro ao registrar a conta: {e}")
                    else:
                        st.warning("Todos os campos do formulário são obrigatórios.")
                        
        with aba_recuperar:
            st.subheader("🛠️ Redefinição de Credencial")
            if st.session_state["recovery_attempts"] >= max_attempts:
                st.error("Você excedeu o número máximo de tentativas de recuperação de senha. Tente novamente mais tarde.")
            else:
                usr_recup = st.text_input("Digite o usuário que deseja redefinir:").strip()
                 
                if usr_recup:
                    usr_recup_norm = normalizar_usuario(usr_recup)
                    try:
                        with get_conn() as conn:
                            dados_usr = conn.execute("SELECT pergunta_seguranca, aprovado, resposta_seguranca_hash, usuario FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usr_recup_norm,)).fetchone()
                    except Exception as db_err:
                        st.error(f"❌ Erro ao consultar banco de dados: {db_err}")
                        dados_usr = None
                     
                    if dados_usr:
                        db_usr = dados_usr[3]
                        st.info(f"Pergunta de Segurança: **{dados_usr[0]}**")
                        resp_recup = st.text_input("Digite a sua resposta secreta:", type="password").strip().lower()
                        nova_senha = st.text_input("Digite a sua Nova Senha:", type="password")
                         
                        if st.button("💾 Gravar Nova Senha"):
                            if resp_recup and nova_senha:
                                import re
                                if len(nova_senha) < 8 or not re.search(r"[a-zA-Z]", nova_senha) or not re.search(r"\d", nova_senha):
                                    st.error("❌ A nova senha deve conter pelo menos 8 caracteres, incluindo pelo menos uma letra e um número.")
                                else:
                                    from utils.security import verificar_senha
                                    hash_salvo_resp = dados_usr[2]
                                    if verificar_senha(resp_recup, hash_salvo_resp):
                                        try:
                                            with get_conn() as conn:
                                                conn.execute("UPDATE usuarios SET senha_hash = ?, resposta_seguranca_hash = ? WHERE usuario = ?", (gerar_hash_senha(nova_senha), gerar_hash_senha(resp_recup), db_usr))
                                             
                                            st.session_state["recovery_attempts"] = 0
                                            registrar_log_auditoria(db_usr, "Recuperação de Senha", f"Usuário '{db_usr}' redefiniu sua senha de acesso via pergunta de segurança.")
                                             
                                            disparar_sincronizacao()
                                            st.success("✅ Senha redefinida com sucesso! Pode voltar para a tela de login.")
                                        except Exception as db_err:
                                            st.error(f"❌ Erro ao atualizar senha no banco: {db_err}")
                                    else:
                                        st.session_state["recovery_attempts"] += 1
                                        tentativas_restantes = max_attempts - st.session_state["recovery_attempts"]
                                        st.error(f"❌ Resposta de segurança incorreta. Tente novamente. Restam {tentativas_restantes} tentativas.")
                            else:
                                st.warning("Preencha a resposta secreta e a nova senha para continuar.")
                    else:
                        st.error("Usuário não encontrado na base do sistema.")
