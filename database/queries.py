import streamlit as st
import pandas as pd
from database.connection import get_conn, retry_db_operation, DB_PATH
from utils.date_helpers import formatar_timestamp_utc

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
def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time, criticidade='Y'):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time, criticidade) VALUES (?, 0, ?, ?, ?, ?, ?)", 
                (nome, estoque_minimo, valor_unitario, categoria, lead_time, criticidade)
            )
        return True, "Sucesso"
    except Exception as e: 
        return False, str(e)

@retry_db_operation()
def editar_produto(id_p, nome, min_e, valor, cat, lead, criticidade='Y'):
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE produtos SET nome=?, estoque_minimo=?, valor_unitario=?, categoria=?, lead_time=?, criticidade=? WHERE id=?", 
                (nome, min_e, valor, cat, lead, criticidade, id_p)
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
def registrar_entrada_produto(id_produto, quantidade, preco_unitario, observacao=""):
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT saldo_atual, valor_unitario FROM produtos WHERE id = ?", (id_produto,)).fetchone()
            if not row:
                return False, "Produto não encontrado."

            saldo_atual, valor_atual = row
            novo_saldo = saldo_atual + quantidade
            novo_pmp = ((saldo_atual * valor_atual) + (quantidade * preco_unitario)) / novo_saldo if novo_saldo > 0 else preco_unitario
            data = formatar_timestamp_utc()
            obs_completa = f"{observacao} | Pago: R$ {preco_unitario:.2f}/un" if observacao.strip() else f"Pago: R$ {preco_unitario:.2f}/un"

            conn.execute(
                "UPDATE produtos SET saldo_atual = ?, valor_unitario = ? WHERE id = ?",
                (novo_saldo, novo_pmp, id_produto)
            )
            conn.execute(
                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Entrada', ?, ?, ?)",
                (id_produto, data, quantidade, novo_saldo, obs_completa)
            )
        return True, "Sucesso"
    except Exception as e:
        return False, str(e)

@retry_db_operation()
def registrar_saida_produto(id_produto, quantidade, observacao=""):
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT saldo_atual FROM produtos WHERE id = ?", (id_produto,)).fetchone()
            if not row:
                return False, "Produto não encontrado."

            saldo_atual = row[0]
            if quantidade > saldo_atual:
                return False, "Estoque insuficiente para a retirada solicitada."

            novo_saldo = saldo_atual - quantidade
            data = formatar_timestamp_utc()
            obs_final = observacao.strip() if observacao.strip() else ""

            conn.execute(
                "UPDATE produtos SET saldo_atual = ? WHERE id = ?",
                (novo_saldo, id_produto)
            )
            conn.execute(
                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Saída', ?, ?, ?)",
                (id_produto, data, -quantidade, novo_saldo, obs_final)
            )
        return True, "Sucesso"
    except Exception as e:
        return False, str(e)

@retry_db_operation()
def registrar_log_auditoria(usuario, acao, detalhes=""):
    import streamlit as st
    
    # Rastreamento de metadados de rede/cliente do Streamlit 1.30+
    client_ip = "127.0.0.1"
    user_agent = "Desconhecido"
    try:
        # st.context está disponível a partir da versão 1.30
        if hasattr(st, "context"):
            client_ip = st.context.ip_address or "127.0.0.1"
            user_agent = st.context.headers.get("User-Agent", "Desconhecido")
    except Exception:
        pass
        
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO logs_auditoria (usuario, acao, data_hora, detalhes, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                (usuario, acao, formatar_timestamp_utc(), detalhes, client_ip, user_agent)
            )
        return True
    except Exception:
        # Fallback caso a tabela logs_auditoria ainda não tenha sido alterada (retrocompatibilidade)
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO logs_auditoria (usuario, acao, data_hora, detalhes) VALUES (?, ?, ?, ?)",
                    (usuario, acao, formatar_timestamp_utc(), detalhes)
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
        
        ids_to_delete = df_arquivar['id'].tolist()
        
        with get_conn() as conn:
            for i in range(0, len(ids_to_delete), 900):
                chunk = ids_to_delete[i:i+900]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM logs_auditoria WHERE id IN ({placeholders})", chunk)
            
        # Otimização física do banco SQLite: executa VACUUM fora de transação
        import sqlite3
        try:
            conn_vac = sqlite3.connect(DB_PATH)
            conn_vac.isolation_level = None  # Modo autocommit
            conn_vac.execute("VACUUM;")
            conn_vac.close()
        except Exception:
            pass
            
        return True, csv_content, len(df_arquivar)
    except Exception as e:
        return False, str(e), 0

def executar_checkpoint_wal():
    """
    Força o SQLite a transferir os logs de escrita (-wal) para o banco principal (.db).
    Ajuda a economizar espaço e evita corrupção de arquivos em desligamentos abruptos.
    """
    try:
        with get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
    except Exception:
        pass

def limpar_cache_consultas():
    """
    Invalida de forma granular apenas o cache das consultas de listagem de produtos
    e movimentações, mantendo outros caches da aplicação intactos. Executa o checkpoint do WAL.
    """
    try:
        listar_produtos.clear()
    except Exception:
        pass
    try:
        listar_movimentacoes.clear()
    except Exception:
        pass
    executar_checkpoint_wal()

