import uuid
from datetime import datetime
import streamlit as st
from database.connection import get_conn
from utils.security import gerar_hash_senha, normalizar_usuario
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria

def render_auth_ui():
    st.title("🔒 WMS Inteligente - Controle de Acesso")
    st.caption("Autenticação obrigatória para acesso à base operacional.")

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
        st.subheader("📝 Solicitar Novo Acesso Operacional")
        st.info("Nota: Após concluir o envio, seu cadastro ficará retido em uma fila de espera até que o administrador aprove.")
        with st.form("form_cadastro"):
            new_usr = st.text_input("Escolha um Nome de Usuário").strip()
            new_pass = st.text_input("Escolha uma Senha", type="password")
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
        st.subheader("== Redefinição de Credencial ==")
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

    # Seção temporária de diagnóstico do banco de dados (remover após a resolução)
    st.write("")
    with st.expander("🔍 Diagnóstico do Banco de Dados (Suporte Técnico)"):
        try:
            import os
            from database.connection import DB_PATH
            st.write(f"**Caminho do Banco:** `{DB_PATH}`")
            if os.path.exists(DB_PATH):
                st.write(f"**Tamanho do Banco:** `{os.path.getsize(DB_PATH)} bytes`")
                st.write(f"**Última Modificação Local:** `{datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime('%Y-%m-%d %H:%M:%S')}`")
            else:
                st.write("⚠️ Arquivo do banco de dados não encontrado localmente!")
            
            with get_conn() as conn:
                # Verificar tabelas
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                st.write(f"**Tabelas encontradas:** `{[t[0] for t in tables]}`")
                
                # Verificar usuários cadastrados
                if any(t[0] == 'usuarios' for t in tables):
                    usrs = conn.execute("SELECT usuario, aprovado, perfil FROM usuarios").fetchall()
                    st.write(f"**Usuários na base:** `{usrs}`")
                else:
                    st.write("⚠️ Tabela de usuários não existe!")
                    
                # Verificar status do sincronismo
                if any(t[0] == 'status_sincronismo' for t in tables):
                    sync_status = conn.execute("SELECT * FROM status_sincronismo").fetchall()
                    st.write(f"**Status de Sincronização:** `{sync_status}`")
                    
                # Verificar configurações
                if any(t[0] == 'configuracoes' for t in tables):
                    configs = conn.execute("SELECT * FROM configuracoes").fetchall()
                    st.write(f"**Configurações do Banco:** `{configs}`")
        except Exception as diag_err:
            st.error(f"Erro ao rodar diagnóstico: {diag_err}")

