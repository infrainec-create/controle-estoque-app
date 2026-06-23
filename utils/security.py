import hashlib
import os
import streamlit as st
from database.connection import get_conn

def gerar_hash_senha(senha):
    """
    Gera um hash robusto usando PBKDF2-SHA256 com um salt aleatório.
    Retorna no formato pbkdf2_sha256$<iterations>$<salt>$<hash>
    """
    salt = os.urandom(16).hex()
    iterations = 100000
    dk = hashlib.pbkdf2_hmac('sha256', senha.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"

def verificar_senha(senha_digitada, hash_armazenado):
    """
    Verifica se a senha digitada corresponde ao hash armazenado.
    Suporta hashes antigos (SHA-256 direto) e novos (PBKDF2).
    """
    if not hash_armazenado:
        return False
        
    if hash_armazenado.startswith("pbkdf2_sha256$"):
        try:
            parts = hash_armazenado.split("$")
            if len(parts) == 4:
                _, iterations_str, salt, hash_hex = parts
                iterations = int(iterations_str)
                dk = hashlib.pbkdf2_hmac('sha256', senha_digitada.encode(), salt.encode(), iterations)
                return dk.hex() == hash_hex
        except Exception:
            return False
    else:
        # Legado: SHA-256 direto
        legacy_hash = hashlib.sha256(senha_digitada.encode()).hexdigest()
        return legacy_hash == hash_armazenado

def verificar_e_atualizar_senha(usuario, senha_digitada, hash_armazenado):
    """
    Verifica a senha. Se for válida e estiver no formato legado,
    atualiza o hash no banco de dados para o formato novo (PBKDF2).
    """
    valida = verificar_senha(senha_digitada, hash_armazenado)
    if valida and not hash_armazenado.startswith("pbkdf2_sha256$"):
        novo_hash = gerar_hash_senha(senha_digitada)
        try:
            with get_conn() as conn:
                conn.execute("UPDATE usuarios SET senha_hash = ? WHERE usuario = ?", (novo_hash, usuario))
        except Exception:
            pass  # Não quebra o login se falhar ao atualizar o hash
    return valida

def limpar_sessoes_expiradas():
    """
    Remove sessões que já expiraram (mais de 2 horas) da base de dados
    para economizar espaço e evitar lixo no SQLite.
    """
    from datetime import datetime, timedelta
    try:
        limite = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            conn.execute("DELETE FROM sessoes WHERE data_criacao < ?", (limite,))
    except Exception:
        pass

def inicializar_estados_sessao():
    # Executa a limpeza automática de tokens expirados a cada carga da aplicação
    limpar_sessoes_expiradas()
    
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False
        st.session_state["usuario_atual"] = ""
        st.session_state["perfil_atual"] = ""

