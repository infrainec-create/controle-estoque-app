import streamlit as st
import pandas as pd
from database.connection import get_conn, retry_db_operation

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

@retry_db_operation()
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

@retry_db_operation()
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

@retry_db_operation()
def deletar_produto(id_produto):
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
            conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))
        return True, "Sucesso"
    except Exception as e:
        return False, str(e)

@retry_db_operation()
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

def arquivar_logs_antigos(dias=90):
    """
    Exporta logs mais antigos que N dias para uma string CSV e os remove do SQLite.
    Usa remoção em lote por ID sequencial (range) para evitar lentidão e o limite do SQLite.
    """
    from datetime import datetime, timedelta
    limite = datetime.now() - timedelta(days=dias)
    
    try:
        with get_conn() as conn:
            df_logs = pd.read_sql("SELECT id, data_hora, usuario, acao, detalhes FROM logs_auditoria", conn)
            
        if df_logs.empty:
            return False, "Nenhum log encontrado para arquivar.", 0
            
        df_logs['dt'] = pd.to_datetime(df_logs['data_hora'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
        df_arquivar = df_logs[df_logs['dt'] < limite].copy()
        
        if df_arquivar.empty:
            return False, f"Nenhum log anterior a {limite.strftime('%d/%m/%Y')} foi encontrado.", 0
            
        # Exportação de colunas formatadas
        df_export = df_arquivar.drop(columns=['dt']).rename(columns={
            'id': 'ID Registro',
            'data_hora': 'Data/Hora',
            'usuario': 'Operador',
            'acao': 'Acao',
            'detalhes': 'Detalhes Operacionais'
        })
        csv_content = df_export.to_csv(index=False, encoding='utf-8-sig')
        
        # Range seguro de IDs para exclusão sem atingir limite de parâmetros do SQLite
        min_id = int(df_arquivar['id'].min())
        max_id = int(df_arquivar['id'].max())
        
        with get_conn() as conn:
            conn.execute("DELETE FROM logs_auditoria WHERE id >= ? AND id <= ?", (min_id, max_id))
            
        return True, csv_content, len(df_arquivar)
    except Exception as e:
        return False, str(e), 0

