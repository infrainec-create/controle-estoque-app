from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from database.connection import get_conn
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria
from utils.backup import realizar_backup_local

def render_audit_ui(df):
    st.subheader("📋 Auditoria de Inventário Diária/Semanal")
    if df.empty:
        st.info("Nenhum insumo disponível para auditoria física.")
        return

    hoje = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y")
    with get_conn() as conn:
        query_hoje = "SELECT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora LIKE ?"
        contados_hoje_df = pd.read_sql(query_hoje, conn, params=(f"{hoje}%",))
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
            
            realizar_backup_local()
            disparar_sincronizacao()
            st.toast(f"📋 Inventário gravado!", icon="💾")
            st.rerun()

    if ids_contados_hoje:
        st.success(f"📌 Excelente! Você já auditou {len(set(ids_contados_hoje))} insumos na data de hoje ({hoje}).")

    st.divider()
    st.subheader("📉 Relatório de Ajustes e Perdas do Inventário")
    
    col_fa1, col_fa2 = st.columns(2)
    with col_fa1:
        opcao_tempo_aud = st.selectbox(
            "Selecione o Intervalo das Auditorias:",
            ["Últimos 30 dias", "Últimos 60 dias (2 Meses)", "Últimos 90 dias (3 Meses)", "Todo o Histórico", "Personalizado"],
            index=0
        )
    
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
    params_q = []
    if prod_aud_sel != "Todos os Insumos":
        query_hist += " AND p.nome = ?"
        params_q.append(prod_aud_sel)
    query_hist += " ORDER BY m.id DESC"
    
    with get_conn() as conn:
        hist_inv = pd.read_sql(query_hist, conn, params=params_q)
        
    if not hist_inv.empty:
        # Converter Data/Hora para datetime para filtragem temporal
        hist_inv['dt'] = pd.to_datetime(hist_inv['Data/Hora'], format='%d/%m/%Y %H:%M', errors='coerce')
        mask_nat = hist_inv['dt'].isna()
        if mask_nat.any():
            hist_inv.loc[mask_nat, 'dt'] = pd.to_datetime(hist_inv.loc[mask_nat, 'Data/Hora'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
            
        hoje_now = pd.Timestamp.now().normalize()
        
        if opcao_tempo_aud == "Últimos 30 dias":
            data_inicio = hoje_now - pd.Timedelta(days=30)
            data_fim = hoje_now
        elif opcao_tempo_aud == "Últimos 60 dias (2 Meses)":
            data_inicio = hoje_now - pd.Timedelta(days=60)
            data_fim = hoje_now
        elif opcao_tempo_aud == "Últimos 90 dias (3 Meses)":
            data_inicio = hoje_now - pd.Timedelta(days=90)
            data_fim = hoje_now
        elif opcao_tempo_aud == "Todo o Histórico":
            data_inicio = hist_inv['dt'].min() if not hist_inv['dt'].isna().all() else hoje_now
            data_fim = hoje_now
        else: # Personalizado
            with col_fa2:
                data_range_aud = st.date_input(
                    "Intervalo de Datas da Auditoria:",
                    value=(hoje_now.date() - pd.Timedelta(days=30), hoje_now.date()),
                    key="range_aud"
                )
                if isinstance(data_range_aud, tuple) and len(data_range_aud) == 2:
                    data_inicio = pd.Timestamp(data_range_aud[0])
                    data_fim = pd.Timestamp(data_range_aud[1])
                else:
                    data_inicio = hoje_now - pd.Timedelta(days=30)
                    data_fim = hoje_now
                    
        # Filtra
        hist_inv_filtrado = hist_inv[(hist_inv['dt'] >= data_inicio) & (hist_inv['dt'] <= data_fim + pd.Timedelta(days=1))].copy()
        
        if not hist_inv_filtrado.empty:
            df_mostrar = hist_inv_filtrado.drop(columns=['dt'])
            
            # KPI de perdas / sobras no período
            total_divergencias = df_mostrar['Divergência'].sum()
            perdas_totais = df_mostrar[df_mostrar['Divergência'] < 0]['Divergência'].sum()
            sobras_totais = df_mostrar[df_mostrar['Divergência'] > 0]['Divergência'].sum()
            
            ck_1, ck_2, ck_3 = st.columns(3)
            ck_1.metric("⚖️ Balanço de Ajustes", f"{int(total_divergencias)} un", 
                         help="Balanço total de divergências no período selecionado.")
            ck_2.metric("📉 Perdas Totais", f"{int(abs(perdas_totais))} un", delta_color="inverse")
            ck_3.metric("📈 Sobras Identificadas", f"{int(sobras_totais)} un")
            
            st.write("---")
            
            def cor_divergencia(val):
                if val < 0: return 'color: #ef4444; font-weight: bold;'
                if val > 0: return 'color: #10b859; font-weight: bold;'
                return 'color: #94a3b8;'
            st.dataframe(df_mostrar.style.map(cor_divergencia, subset=['Divergência']), hide_index=True, width='stretch')
        else:
            st.warning("⚠️ Nenhum registro de inventário encontrado para o período selecionado.")
    else:
        st.info("Nenhum registro de inventário cadastrado ainda.")
