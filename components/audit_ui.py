from datetime import datetime
import pandas as pd
import streamlit as st
from database.connection import get_conn
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria
from utils.backup import realizar_backup_local
from utils.consumption import obter_agora_fortaleza

def render_audit_ui(df):
    st.subheader("📋 Auditoria de Inventário & Gestão de Divergências")
    st.caption("Conferência de saldo físico na prateleira vs. saldo registrado no sistema WMS.")
    
    if df.empty:
        st.info("Nenhum insumo disponível para auditoria física.")
        return

    hoje = obter_agora_fortaleza().strftime("%d/%m/%Y")
    with get_conn() as conn:
        query_hoje = "SELECT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora LIKE ?"
        contados_hoje_df = pd.read_sql(query_hoje, conn, params=(f"{hoje}%",))
        cnt_ira = conn.execute("SELECT COUNT(*), SUM(CASE WHEN quantidade = 0 THEN 1 ELSE 0 END) FROM movimentacoes WHERE tipo = 'Contagem'").fetchone()
    
    ids_contados_hoje = contados_hoje_df['id_produto'].tolist()
    
    # ─── HEADER EXECUTIVO DE AUDITORIA ───
    tot_auditorias = cnt_ira[0] if cnt_ira else 0
    corretas_auditorias = cnt_ira[1] if cnt_ira and cnt_ira[1] is not None else 0
    ira_score = (corretas_auditorias / tot_auditorias * 100.0) if tot_auditorias > 0 else 100.0
    
    aud_col1, aud_col2, aud_col3, aud_col4 = st.columns(4)
    aud_col1.metric("📋 Acuracidade IRA", f"{ira_score:.1f}%", f"{corretas_auditorias}/{tot_auditorias} corretas")
    aud_col2.metric("✅ Contados Hoje", f"{len(set(ids_contados_hoje))} insumos")
    aud_col3.metric("📦 Total para Audit", f"{len(df)} insumos")
    aud_col4.metric("👤 Auditado Por", st.session_state.get("usuario_atual", "Operador"))

    # ─── BARRA DE PROGRESSO DA META CÍCLICA SEMANAL (7 DIAS) ───
    agora_dt = obter_agora_fortaleza()
    dt_7d_str = (agora_dt - pd.Timedelta(days=7)).strftime("%d/%m/%Y")
    try:
        with get_conn() as conn:
            contados_sem_df = pd.read_sql("SELECT DISTINCT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora >= ?", conn, params=(dt_7d_str,))
            ids_contados_sem = set(contados_sem_df['id_produto'].tolist())
    except Exception:
        ids_contados_sem = set(ids_contados_hoje)
        
    n_contados_sem = len(ids_contados_sem)
    tot_itens_audit = len(df)
    pct_semana = (n_contados_sem / tot_itens_audit) if tot_itens_audit > 0 else 0.0
    
    st.markdown(f"**📊 Meta Semanal de Auditoria Cíclica:** **{n_contados_sem}** de **{tot_itens_audit}** insumos auditados nos últimos 7 dias (**{pct_semana * 100:.1f}%**)")
    st.progress(min(1.0, max(0.0, float(pct_semana))))

    st.divider()

    # ─── INVENTÁRIO CÍCLICO GUIADO POR INTELIGÊNCIA (MATRIZ ABC-XYZ) ───
    df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]
    df_abc = df.sort_values(by="valor_total", ascending=False).copy()
    tot_val = df_abc["valor_total"].sum()
    if tot_val > 0:
        df_abc["perc_acum"] = (df_abc["valor_total"].cumsum() / tot_val) * 100
        df_abc["Classe_ABC"] = df_abc["perc_acum"].apply(lambda p: "A" if p <= 80 else ("B" if p <= 95 else "C"))
    else:
        df_abc["Classe_ABC"] = "C"
        
    df["Classe_ABC"] = df["id"].map(dict(zip(df_abc["id"], df_abc["Classe_ABC"]))).fillna("C")
    df["criticidade"] = df["criticidade"].fillna("Y").str.upper()

    def priority_score(row):
        abc = row["Classe_ABC"]
        xyz = row["criticidade"]
        if abc == "A" and xyz == "Z": return 1
        if abc == "A" or xyz == "Z": return 2
        if abc == "B" or xyz == "Y": return 3
        return 4

    df["Score_Prioridade"] = df.apply(priority_score, axis=1)
    df_ciclico = df[~df["id"].isin(ids_contados_hoje)].sort_values(by=["Score_Prioridade", "valor_total"], ascending=[True, False]).head(5)

    with st.expander("🎯 Plano de Contagem Cíclica Guiada do Dia (Priorização ABC-XYZ)", expanded=True):
        st.caption("Sugestão automática de auditoria por amostragem baseada no risco financeiro (ABC) e criticidade operacional (XYZ).")
        if not df_ciclico.empty:
            c_items = st.columns(len(df_ciclico))
            for idx, (_, item) in enumerate(df_ciclico.iterrows()):
                prio_badge = "🔴 Vital (A-Z)" if item["Score_Prioridade"] == 1 else ("🟠 Alta (A/Z)" if item["Score_Prioridade"] == 2 else "🟡 Média")
                with c_items[idx]:
                    st.markdown(f"**{item['nome']}**")
                    st.caption(f"Setor: {item['categoria']} | {prio_badge}")
                    st.caption(f"Saldo: {int(item['saldo_atual'])} un")
                    if st.button(f"🔍 Audit #{item['id']}", key=f"btn_cic_{item['id']}", use_container_width=True):
                        st.session_state["c_p_sel_id"] = item["id"]
                        st.rerun()
        else:
            st.success("🎉 Todos os insumos prioritários de hoje já foram auditados com sucesso!")

    with st.container(border=True):
        st.markdown("##### ✏️ Registrar Nova Contagem Física")
        
        # Filtro por Setor para agilizar a seleção no inventário
        setor_aud = st.selectbox("⚡ Filtrar Insumos por Setor:", ["Todos os Setores"] + list(df["categoria"].unique()), key="aud_setor")
        df_aud_sel = df[df["categoria"] == setor_aud] if setor_aud != "Todos os Setores" else df
        
        ops = {}
        for _, row in df_aud_sel.iterrows():
            nome_exib = f"✅ {row['nome']} (Auditado Hoje)" if row['id'] in ids_contados_hoje else row['nome']
            ops[nome_exib] = row['id']
        
        c_a1, c_a2 = st.columns([2, 1])
        with c_a1:
            sel_c = st.selectbox("Selecione o Insumo para Contagem:", list(ops.keys()), key="c_p")
            id_pc = ops[sel_c]
            s_sis = int(df.loc[df["id"]==id_pc, "saldo_atual"].values[0])
        with c_a2:
            st.metric("Saldo Sistema", f"{s_sis} un")

        c_q1, c_q2 = st.columns([1, 2])
        with c_q1:
            f_cont = st.number_input("Quantidade Física Contada", min_value=0, value=s_sis, step=1, key="c_q")
        with c_q2:
            diff = f_cont - s_sis
            if diff == 0:
                st.success("🟢 Contagem exata! Nenhuma divergência detectada.")
            elif diff > 0:
                st.info(f"📈 Sobra física identificada: **+{diff} un.** em relação ao sistema.")
            else:
                st.warning(f"📉 Falta física / Perda identificada: **{diff} un.** em relação ao sistema.")
        
        if st.button("💾 Gravar Ajuste de Inventário", use_container_width=True, type="primary"):
            with get_conn() as conn:
                conn.execute("UPDATE produtos SET saldo_atual = ? WHERE id = ?", (f_cont, id_pc))
                data = obter_agora_fortaleza().strftime("%d/%m/%Y %H:%M")
                obs_inv = f"Inventário Semanal | Op: {st.session_state['usuario_atual']}"
                conn.execute("INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Contagem', ?, ?, ?)", (id_pc, data, diff, f_cont, obs_inv))
            
            detalhes_log = f"Realizou contagem física do insumo '{sel_c}'. Saldo no sistema: {s_sis} un., Físico: {f_cont} un. Divergência: {diff} un."
            registrar_log_auditoria(st.session_state["usuario_atual"], "Ajuste de Inventário", detalhes_log)
            
            realizar_backup_local()
            disparar_sincronizacao()
            st.toast("📋 Inventário gravado!", icon="💾")
            st.rerun()

    st.divider()
    st.markdown("##### 📉 Histórico & Relatório de Divergências de Inventário")
    
    col_fa1, col_fa2 = st.columns(2)
    with col_fa1:
        opcao_tempo_aud = st.selectbox(
            "Intervalo das Auditorias:",
            ["Últimos 30 dias", "Últimos 60 dias (2 Meses)", "Últimos 90 dias (3 Meses)", "Todo o Histórico", "Personalizado"],
            index=0
        )
    
    prod_lista = ["Todos os Insumos"] + list(df["nome"].unique())
    prod_aud_sel = st.selectbox("Filtrar auditorias por insumo:", prod_lista)
    
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
        else:
            with col_fa2:
                data_range_aud = st.date_input(
                    "Intervalo de Datas:",
                    value=(hoje_now.date() - pd.Timedelta(days=30), hoje_now.date()),
                    key="range_aud"
                )
                if isinstance(data_range_aud, tuple) and len(data_range_aud) == 2:
                    data_inicio = pd.Timestamp(data_range_aud[0])
                    data_fim = pd.Timestamp(data_range_aud[1])
                else:
                    data_inicio = hoje_now - pd.Timedelta(days=30)
                    data_fim = hoje_now
                    
        hist_inv_filtrado = hist_inv[(hist_inv['dt'] >= data_inicio) & (hist_inv['dt'] <= data_fim + pd.Timedelta(days=1))].copy()
        
        if not hist_inv_filtrado.empty:
            df_mostrar = hist_inv_filtrado.drop(columns=['dt'])
            
            total_divergencias = df_mostrar['Divergência'].sum()
            perdas_totais = df_mostrar[df_mostrar['Divergência'] < 0]['Divergência'].sum()
            sobras_totais = df_mostrar[df_mostrar['Divergência'] > 0]['Divergência'].sum()
            
            ck_1, ck_2, ck_3 = st.columns(3)
            ck_1.metric("⚖️ Balanço de Ajustes", f"{int(total_divergencias)} un")
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
