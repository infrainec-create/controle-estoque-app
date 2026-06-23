import uuid
from datetime import datetime
import streamlit as st
from database.connection import get_conn
from utils.security import gerar_hash_senha
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria

def render_auth_ui():
    st.title("🔒 WMS Inteligente - Controle de Acesso")
    st.caption("Autenticação obrigatória para acesso à base operacional.")
    
    aba_login, aba_cadastro, aba_recuperar = st.tabs(["🔑 Entrar no Sistema", "👤 Criar Conta", "🛠️ Esqueci a Senha"])
    
    with aba_login:
        with st.form("form_login"):
            usr_input = st.text_input("Usuário (Login)").strip()
            pass_input = st.text_input("Senha", type="password")
            btn_login = st.form_submit_button("Acessar WMS")
            
            if btn_login:
                if usr_input and pass_input:
                    with get_conn() as conn:
                        res = conn.execute("SELECT aprovado, perfil, senha_hash, usuario FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usr_input,)).fetchone()
                    
                    from utils.security import verificar_e_atualizar_senha
                    if res and verificar_e_atualizar_senha(res[3], pass_input, res[2]):
                        aprovado, perfil, _, db_usr = res
                        if aprovado == 1:
                            # Geração e persistência da sessão no banco de dados e URL
                            session_token = str(uuid.uuid4())
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            with get_conn() as conn:
                                conn.execute("INSERT OR REPLACE INTO sessoes (token, usuario, data_criacao) VALUES (?, ?, ?)", (session_token, db_usr, now_str))
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
                        st.error("❌ Usuário ou senha incorretos. Verifique suas credenciais.")
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
                    # Validação de complexidade da senha (mínimo 8 caracteres, pelo menos uma letra e um número)
                    if len(new_pass) < 8 or not re.search(r"[a-zA-Z]", new_pass) or not re.search(r"\d", new_pass):
                        st.error("❌ A senha deve conter pelo menos 8 caracteres, incluindo pelo menos uma letra e um número.")
                    else:
                        try:
                            with get_conn() as conn:
                                # Verificação case-insensitive de existência de usuário
                                exists = conn.execute("SELECT 1 FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (new_usr,)).fetchone()
                                
                                if exists:
                                    st.error("❌ Esse nome de usuário já existe na base. Tente uma combinação diferente.")
                                else:
                                    # Se for o primeiro usuário no banco, torna-se administrador automaticamente (bootstrap seguro)
                                    total_usuarios = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
                                    if total_usuarios == 0:
                                        status_inicial = 1
                                        perfil_inicial = "Administrador"
                                    else:
                                        status_inicial = 0
                                        perfil_inicial = "Operador"
                                        
                                    conn.execute(
                                        "INSERT INTO usuarios (usuario, senha_hash, pergunta_seguranca, resposta_seguranca_hash, aprovado, perfil) VALUES (?, ?, ?, ?, ?, ?)",
                                        (new_usr, gerar_hash_senha(new_pass), pergunta, gerar_hash_senha(resposta), status_inicial, perfil_inicial)
                                    )
                                    
                                    registrar_log_auditoria(new_usr if status_inicial == 1 else "Sistema", "Solicitação de Cadastro", f"Solicitação de cadastro enviada para o usuário '{new_usr}' (Perfil inicial: {perfil_inicial}, Aprovado: {'Sim' if status_inicial == 1 else 'Não'}).")
                                    
                                    disparar_sincronizacao()
                                    if status_inicial == 1:
                                        st.success("👑 Conta de administrador master criada! Vá para a aba Entrar e realize o login.")
                                    else:
                                        st.success(f"⏳ Solicitação enviada! O usuário '{new_usr}' foi colocado na fila de aceitação do Administrador.")
                        except Exception as e:
                            st.error(f"❌ Ocorreu um erro ao registrar a conta: {e}")
                else:
                    st.warning("Todos os campos do formulário são obrigatórios.")
                    
    with aba_recuperar:
        st.subheader("== Redefinição de Credencial ==")
        usr_recup = st.text_input("Digite o usuário que deseja redefinir:").strip()
        
        if usr_recup:
            with get_conn() as conn:
                dados_usr = conn.execute("SELECT pergunta_seguranca, aprovado, resposta_seguranca_hash, usuario FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usr_recup,)).fetchone()
            
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
                                with get_conn() as conn:
                                    conn.execute("UPDATE usuarios SET senha_hash = ?, resposta_seguranca_hash = ? WHERE usuario = ?", (gerar_hash_senha(nova_senha), gerar_hash_senha(resp_recup), db_usr))
                                
                                registrar_log_auditoria(db_usr, "Recuperação de Senha", f"Usuário '{db_usr}' redefiniu sua senha de acesso via pergunta de segurança.")
                                
                                disparar_sincronizacao()
                                st.success("✅ Senha redefinida com sucesso! Pode voltar para a tela de login.")
                            else:
                                st.error("❌ Resposta de segurança incorreta. Tente novamente.")
                    else:
                        st.warning("Preencha a resposta e a nova senha.")
            else:
                st.error("Usuário não encontrado na base do sistema.")
