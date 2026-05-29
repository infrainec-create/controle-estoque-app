from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from database.connection import get_conn
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria

def render_audit_ui(df):
    st.subheader("📋 Auditoria de Inventário Diária/Semanal")
    if df.empty:
        st.info("Nenhum insumo disponível para auditoria física.")
        return

    hoje = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y")
    with get_conn() as conn:
        query_hoje = f"SELECT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora LIKE '{hoje}%'"
        contados_hoje_df = pd.read_sql(query_hoje, conn)
    ids_contados_hoje = contados_hoje_df['id_produto'].tolist()
    
    with st.container(border=True):
        ops = {}
        for _, row in df.iterrows():
            nome_exib = f"✅ {row['nome']} (Auditado Hoje)" if row['id'] in ids_contados_hoje else row['nome']
            ops[nome_exib] = row['id']
        
        sel_c = st.selectbox("Selecione o Insumo para Contagem:", list(ops.keys()), key="c_p")
        id_pc = ops[sel_c]
        s_sis = int(df.loc[df["id"]==id_pc, "saldo_atual"].values[0])
        st.metric("Saldo Atual no Sistema", f"{s_sis} un")
        f_cont = st.number_input("Quantidade Física Contada", min_value=0, step=1, key="c_q")
        diff = f_cont - s_sis
        
        if st.button("💾 Gravar e Sincronizar Inventário", use_container_width=True, type="primary"):
            with get_conn() as conn:
                conn.execute("UPDATE produtos SET saldo_atual = ? WHERE id = ?", (f_cont, id_pc))
                data = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
                obs_inv = f"Inventário Semanal | Op: {st.session_state['usuario_atual']}"
                conn.execute("INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Contagem', ?, ?, ?)", (id_pc, data, diff, f_cont, obs_inv))
            
            detalhes_log = f"Realizou contagem física do insumo '{sel_c}'. Saldo no sistema: {s_sis} un., Físico: {f_cont} un. Divergência: {diff} un."
            registrar_log_auditoria(st.session_state["usuario_atual"], "Ajuste de Inventário", detalhes_log)
            
            disparar_sincronizacao()
            st.toast(f"📋 Inventário gravado!", icon="💾")
            st.rerun()

    if ids_contados_hoje:
        st.success(f"📌 Excelente! Você já auditou {len(set(ids_contados_hoje))} insumos na data de hoje ({hoje}).")

    st.divider()
    st.subheader("📉 Relatório de Ajustes e Perdas do Inventário")
    prod_lista = ["Todos os Insumos"] + list(df["nome"].unique())
    prod_aud_sel = st.selectbox("Filtrar auditorias por item específico:", prod_lista)
    
    query_hist = """
        SELECT m.data_hora as 'Data/Hora', p.nome as 'Produto', 
               (m.saldo_resultante - m.quantidade) as 'Saldo Anterior',
               m.saldo_resultante as 'Contagem Física',
               m.quantidade as 'Divergência',
               m.observacao as 'Registro'
        FROM movimentacoes m 
        JOIN produtos p ON p.id = m.id_produto
        WHERE m.tipo = 'Contagem'
    """
    if prod_aud_sel != "Todos os Insumos":
        query_hist += f" AND p.nome = '{prod_aud_sel}'"
    query_hist += " ORDER BY m.id DESC LIMIT 15"
    
    with get_conn() as conn:
        hist_inv = pd.read_sql(query_hist, conn)
    if not hist_inv.empty:
        def cor_divergencia(val):
            if val < 0: return 'color: #ef4444; font-weight: bold;'
            if val > 0: return 'color: #10b859; font-weight: bold;'
            return 'color: #94a3b8;'
        st.dataframe(hist_inv.style.map(cor_divergencia, subset=['Divergência']), hide_index=True, width='stretch')
