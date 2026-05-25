import streamlit as st
import pandas as pd
from database.connection import get_conn

@st.cache_data
def listar_produtos():
    with get_conn() as conn: 
        return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)

@st.cache_data
def listar_movimentacoes():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
            FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
        """, conn)

def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time) VALUES (?, 0, ?, ?, ?, ?)", 
                (nome, estoque_minimo, valor_unitario, categoria, lead_time)
            )
        return True, "Sucesso"
    except Exception as e: 
        return False, str(e)

def editar_produto(id_p, nome, min_e, valor, cat, lead):
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE produtos SET nome=?, estoque_minimo=?, valor_unitario=?, categoria=?, lead_time=? WHERE id=?", 
                (nome, min_e, valor, cat, lead, id_p)
            )
        return True, "Sucesso"
    except Exception as e:
        return False, str(e)

def deletar_produto(id_produto):
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
            conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))
        return True, "Sucesso"
    except Exception as e:
        return False, str(e)

def registrar_log_auditoria(usuario, acao, detalhes=""):
    from datetime import datetime
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO logs_auditoria (usuario, acao, data_hora, detalhes) VALUES (?, ?, ?, ?)",
                (usuario, acao, datetime.now().strftime("%d/%m/%Y %H:%M:%S"), detalhes)
            )
        return True
    except Exception:
        return False
