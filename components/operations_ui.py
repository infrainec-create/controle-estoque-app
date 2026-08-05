import streamlit as st

from database.queries import (
    listar_movimentacoes,
    registrar_entrada_produto,
    registrar_log_auditoria,
    registrar_saida_produto,
)
from utils.backup import realizar_backup_local
from utils.drive_sync import disparar_sincronizacao


def render_operations_ui(df):
    st.subheader("⚡ Lançamentos Operacionais de Estoque")
    st.caption("Registro rápido de entradas (ressuprimento) e saídas (requisições/consumo) com validação dinâmica de saldo.")
    
    if df.empty:
        st.info("Nenhum insumo disponível para lançamentos de entrada ou saída.")
        return

    # ─── HEADER EXECUTIVO DE OPERAÇÕES ───
    total_insumos = len(df)
    itens_zerados = (df["saldo_atual"] <= 0).sum()
    itens_criticos = ((df["saldo_atual"] > 0) & (df["saldo_atual"] < df["estoque_minimo"])).sum()
    
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("📦 Insumos Ativos", f"{total_insumos} itens")
    col_m2.metric("🚨 Em Ruptura (Zerados)", f"{itens_zerados} itens", delta_color="inverse")
    col_m3.metric("⚠️ Em Estado Crítico", f"{itens_criticos} itens", delta_color="inverse")
    col_m4.metric("👤 Operador Responsável", st.session_state.get("usuario_atual", "Operador"))

    st.divider()

    col_e, col_s = st.columns(2)
    with col_e:
        with st.container(border=True):
            st.markdown("##### 📥 Registrar Entrada (Ressuprimento)")
            
            # Filtro prévio por Setor para agilizar a seleção de insumos
            setor_e = st.selectbox("⚡ Filtrar Insumos por Setor:", ["Todos os Setores"] + list(df["categoria"].unique()), key="e_setor")
            df_e = df[df["categoria"] == setor_e] if setor_e != "Todos os Setores" else df
            
            ops = dict(zip(df_e["nome"], df_e["id"]))
            if ops:
                sel_e = st.selectbox("Selecione o Insumo:", list(ops.keys()), key="e_p")
                id_pe = ops[sel_e]
                p_atual = df.loc[df["id"]==id_pe].iloc[0]
                s_atual_e = int(p_atual["saldo_atual"])
                pmp_antigo = float(p_atual["valor_unitario"])
                
                st.info(f"Saldo Físico Atual no Sistema: **{s_atual_e} un.** (Preço Unit. Cadastrado: **R$ {pmp_antigo:,.2f}**)")
                
                c1, c2 = st.columns([1, 1])
                with c1:
                    qe = st.number_input("Quantidade de Entrada", min_value=1, value=1, key="e_q")
                with c2:
                    preco_compra = st.number_input("Preço Unit. de Compra (R$)", min_value=0.0, value=pmp_antigo, step=0.01, key="e_v")
                obs_e = st.text_input("Nº Nota Fiscal / Fornecedor / Motivo", key="e_obs")
                    
                if st.button("Confirmar Entrada (Ressuprir)", type="primary", use_container_width=True):
                    sucesso_ent, msg_ent = registrar_entrada_produto(id_pe, qe, preco_compra, obs_e)
                    if sucesso_ent:
                        detalhes_log = f"Registrou entrada de {qe} un. do insumo '{sel_e}' (Preço Pago: R$ {preco_compra:.2f}/un; Total: R$ {qe * preco_compra:.2f})."
                        registrar_log_auditoria(st.session_state["usuario_atual"], "Entrada de Estoque", detalhes_log)

                        realizar_backup_local()
                        disparar_sincronizacao()
                        st.toast("📥 Entrada registrada com sucesso!", icon="✅")
                        st.rerun()
                    else:
                        st.error(f"Erro ao registrar entrada: {msg_ent}")
            else:
                st.warning("Nenhum insumo encontrado para este setor.")

    with col_s:
        with st.container(border=True):
            st.markdown("##### 📤 Registrar Saída (Baixa / Requisição)")
            
            # Filtro prévio por Setor para agilizar a seleção de insumos
            setor_s = st.selectbox("⚡ Filtrar Insumos por Setor:", ["Todos os Setores"] + list(df["categoria"].unique()), key="s_setor")
            df_s = df[df["categoria"] == setor_s] if setor_s != "Todos os Setores" else df
            
            ops_s = dict(zip(df_s["nome"], df_s["id"]))
            if ops_s:
                sel = st.selectbox("Selecione o Insumo:", list(ops_s.keys()), key="s_p")
                id_p = ops_s[sel]
                
                p_atual_s = df.loc[df["id"]==id_p].iloc[0]
                max_s = int(p_atual_s["saldo_atual"])
                est_min_s = int(p_atual_s["estoque_minimo"])
                
                c1, c2 = st.columns([1, 2])
                with c1:
                    q = st.number_input("Qtd. Retirada", min_value=1, value=1, key="s_q")
                with c2:
                    obs_s = st.text_input("Destino / Setor Requisitante", key="s_obs")
                
                saldo_futuro = max_s - q
                bloquear_saida = q > max_s
                
                # --- VALIDAÇÕES E ALERTAS DINÂMICOS PREVENTIVOS ---
                if bloquear_saida:
                    st.error(f"❌ Estoque Insuficiente! Saldo na prateleira: {max_s} un.")
                elif saldo_futuro == 0:
                    st.warning("⚠️ Atenção! Esta retirada irá ZERAR o saldo físico deste insumo em estoque!")
                elif saldo_futuro < est_min_s:
                    st.warning(f"⚠️ Alerta! Esta retirada deixará o saldo ({saldo_futuro} un) ABAIXO do estoque mínimo ({est_min_s} un)!")
                else:
                    st.success(f"🟢 Saldo seguro após retirada: {saldo_futuro} un (Mínimo: {est_min_s} un).")
                    
                if st.button("Confirmar Saída (Dar Baixa)", type="primary", disabled=bloquear_saida, use_container_width=True):
                    sucesso_saida, msg_saida = registrar_saida_produto(id_p, q, obs_s)
                    if sucesso_saida:
                        detalhes_log = f"Registrou saída de {q} un. do insumo '{sel}' (Observação: '{obs_s}'). Saldo restante estimado: {max_s - q} un."
                        registrar_log_auditoria(st.session_state["usuario_atual"], "Saída de Estoque", detalhes_log)

                        realizar_backup_local()
                        disparar_sincronizacao()
                        st.toast("📤 Baixa realizada com sucesso!", icon="🚀")
                        st.rerun()
                    else:
                        st.error(f"Erro ao registrar saída: {msg_saida}")
            else:
                st.warning("Nenhum insumo encontrado para este setor.")

    # ─── MINI-HISTÓRICO RECENTE DE LANÇAMENTOS ───
    st.divider()
    st.markdown("##### 📜 Últimas Movimentações Realizadas no Almoxarifado")
    mv_rec = listar_movimentacoes()
    if not mv_rec.empty:
        st.dataframe(
            mv_rec.head(5)[['data_hora', 'produto', 'tipo', 'quantidade', 'saldo_resultante', 'observacao']].rename(
                columns={
                    'data_hora': 'Data/Hora',
                    'produto': 'Insumo',
                    'tipo': 'Operação',
                    'quantidade': 'Qtd.',
                    'saldo_resultante': 'Saldo Resultante',
                    'observacao': 'Observação / Destino'
                }
            ),
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("Nenhuma movimentação recente registrada.")
