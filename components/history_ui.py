import math

import pandas as pd
import streamlit as st

from utils.reports import gerar_excel_movimentacoes


def render_history_ui(df, mv):
    st.subheader("📜 Histórico Geral & Audit de Movimentações")
    st.caption("Acompanhamento paginado de entradas, saídas e contagens físicas de estoque com busca inteligente.")
    
    if mv.empty:
        st.info("Nenhuma movimentação registrada no histórico.")
        return

    # 1. Parsing robusto de datas para filtragem e ordenação
    mv = mv.copy()
    mv['dt'] = pd.to_datetime(mv['data_hora'], format='%d/%m/%Y %H:%M', errors='coerce')
    mask_nat = mv['dt'].isna()
    if mask_nat.any():
        mv.loc[mask_nat, 'dt'] = pd.to_datetime(mv.loc[mask_nat, 'data_hora'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
        
    # --- CONTROLES DE FILTRO TEMPORAL E OPERACIONAL ---
    st.markdown("##### 🔍 Filtros de Consulta do Histórico")
    col_f1, col_f2, col_f3 = st.columns([2, 2, 2])
    
    with col_f1:
        opcao_tempo = st.selectbox(
            "Intervalo Temporal:",
            ["Últimos 30 dias", "Últimos 60 dias (2 Meses)", "Últimos 90 dias (3 Meses)", "Últimos 180 dias (6 Meses)", "Personalizado", "Todo o Histórico"],
            index=0
        )
        
    hoje = pd.Timestamp.now().normalize()
    
    if opcao_tempo == "Últimos 30 dias":
        data_inicio = hoje - pd.Timedelta(days=30)
        data_fim = hoje
    elif opcao_tempo == "Últimos 60 dias (2 Meses)":
        data_inicio = hoje - pd.Timedelta(days=60)
        data_fim = hoje
    elif opcao_tempo == "Últimos 90 dias (3 Meses)":
        data_inicio = hoje - pd.Timedelta(days=90)
        data_fim = hoje
    elif opcao_tempo == "Últimos 180 dias (6 Meses)":
        data_inicio = hoje - pd.Timedelta(days=180)
        data_fim = hoje
    elif opcao_tempo == "Todo o Histórico":
        data_inicio = mv['dt'].min() if not mv['dt'].isna().all() else hoje
        data_fim = hoje
    else:  # Personalizado
        with col_f2:
            data_range = st.date_input(
                "Datas inicial e final:",
                value=(hoje.date() - pd.Timedelta(days=30), hoje.date()),
                key="range_historico"
            )
            if isinstance(data_range, tuple) and len(data_range) == 2:
                data_inicio = pd.Timestamp(data_range[0])
                data_fim = pd.Timestamp(data_range[1])
            else:
                data_inicio = hoje - pd.Timedelta(days=30)
                data_fim = hoje

    with col_f2 if opcao_tempo != "Personalizado" else col_f3:
        filtro_tipo = st.selectbox(
            "Tipo de Operação:",
            ["Todas as Operações", "Entrada", "Saída", "Contagem (Inventário)"]
        )

    # Aplica os filtros de data e tipo
    mv_filtrado = mv[(mv['dt'] >= data_inicio) & (mv['dt'] <= data_fim + pd.Timedelta(days=1))].copy()
    if filtro_tipo != "Todas as Operações":
        if filtro_tipo == "Contagem (Inventário)":
            mv_filtrado = mv_filtrado[mv_filtrado['tipo'] == 'Contagem']
        else:
            mv_filtrado = mv_filtrado[mv_filtrado['tipo'] == filtro_tipo]

    # Busca textual por Produto ou Detalhes
    col_b1, col_b2 = st.columns([3, 1])
    with col_b1:
        busca_hist = st.text_input("🔍 Pesquisa por Insumo ou Detalhe/Motivo:", "").strip()
    with col_b2:
        itens_por_pagina = st.selectbox("Itens / Pág:", [15, 30, 50, 100], index=0)

    if busca_hist:
        mv_filtrado = mv_filtrado[
            mv_filtrado['produto'].astype(str).str.contains(busca_hist, case=False, na=False) |
            mv_filtrado['observacao'].astype(str).str.contains(busca_hist, case=False, na=False)
        ]

    # --- CARDS DE SÍNTESE DO HISTÓRICO FILTRADO ---
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    
    total_lancamentos = len(mv_filtrado)
    entradas_df = mv_filtrado[mv_filtrado['tipo'] == 'Entrada']
    saidas_df = mv_filtrado[mv_filtrado['tipo'] == 'Saída']
    contagens_df = mv_filtrado[mv_filtrado['tipo'] == 'Contagem']
    
    vol_entradas = entradas_df['quantidade'].sum() if not entradas_df.empty else 0
    vol_saidas = abs(saidas_df['quantidade'].sum()) if not saidas_df.empty else 0
    
    m1.metric("📊 Lançamentos Filtrados", f"{total_lancamentos} registros")
    m2.metric("📥 Total Entradas", f"{len(entradas_df)} ops", f"+{int(vol_entradas)} un.")
    m3.metric("📤 Total Saídas", f"{len(saidas_df)} ops", f"-{int(vol_saidas)} un.")
    m4.metric("📋 Ajustes de Inventário", f"{len(contagens_df)} contagens")

    if mv_filtrado.empty:
        st.warning("⚠️ Nenhuma movimentação corresponde aos critérios de filtragem selecionados.")
        return

    # --- GRÁFICOS COMPARATIVOS ---
    with st.expander("📈 Visualizar Gráficos Comparativos e Curva de Custos do Histórico", expanded=False):
        st.markdown("##### 📊 Comparativo de Consumo Mensal (Saídas de Estoque)")
        saidas_geral = mv[mv['tipo'] == 'Saída'].copy()
        if not saidas_geral.empty:
            saidas_geral['Mês/Ano'] = saidas_geral['dt'].dt.strftime('%m/%Y')
            saidas_geral['AnoMes'] = saidas_geral['dt'].dt.to_period('M')
            
            consumo_mensal = saidas_geral.groupby(['AnoMes', 'produto'])['quantidade'].apply(lambda x: x.abs().sum()).reset_index()
            consumo_mensal['Período (Mês/Ano)'] = consumo_mensal['AnoMes'].astype(str)
            consumo_mensal.rename(columns={'quantidade': 'Consumo Total (un)'}, inplace=True)
            
            produtos_saida = ["Todos os Insumos"] + list(saidas_geral['produto'].unique())
            prod_sel_comp = st.selectbox("Filtrar gráfico comparativo por item:", produtos_saida, key="prod_sel_comp_hist")
            
            if prod_sel_comp != "Todos os Insumos":
                dados_graf = consumo_mensal[consumo_mensal['produto'] == prod_sel_comp]
            else:
                dados_graf = consumo_mensal.groupby('Período (Mês/Ano)')['Consumo Total (un)'].sum().reset_index()
                
            if not dados_graf.empty:
                dados_graf = dados_graf.sort_values(by="Período (Mês/Ano)")
                st.bar_chart(data=dados_graf, x="Período (Mês/Ano)", y="Consumo Total (un)", color="#3b82f6")
            else:
                st.info("Nenhum consumo registrado para plotagem comparativa.")
                
        st.markdown("##### 📈 Evolução do Preço de Compra (Entradas)")
        if not df.empty:
            item_analise = st.selectbox("Insumo para Análise de Custo:", list(df["nome"].unique()))
            entradas_item = mv_filtrado[(mv_filtrado["produto"] == item_analise) & (mv_filtrado["tipo"] == "Entrada")].copy()
            
            if not entradas_item.empty:
                def extrair_preco(obs):
                    try:
                        if "Pago: R$" in str(obs):
                            return float(str(obs).split("Pago: R$ ")[1].split("/un")[0])
                    except (ValueError, IndexError, AttributeError):
                        pass
                    return None
                
                entradas_item["Preço de Compra (R$)"] = entradas_item["observacao"].apply(extrair_preco)
                entradas_item = entradas_item.dropna(subset=["Preço de Compra (R$)"]).iloc[::-1]
                if not entradas_item.empty: 
                    st.line_chart(data=entradas_item, x="data_hora", y="Preço de Compra (R$)")
                else:
                    st.info("Nenhum preço de compra detalhado foi encontrado para este produto no período.")

    # --- TABELA PAGINADA DE HISTÓRICO GERAL ---
    st.divider()
    st.markdown("##### 📋 Tabela de Histórico Geral Paginada")
    
    # Preparar DataFrame para exibição limpa
    df_exibicao = mv_filtrado.drop(columns=['dt']).copy()
    
    # Lógica de Paginação Dinâmica
    total_registros = len(df_exibicao)
    total_paginas = max(1, math.ceil(total_registros / itens_por_pagina))
    
    # Resetar página caso o filtro reduza o total de páginas abaixo da página atual
    if "pag_hist_atual" not in st.session_state or st.session_state["pag_hist_atual"] > total_paginas:
        st.session_state["pag_hist_atual"] = 1

    pag_atual = st.session_state["pag_hist_atual"]
    
    # Controles de Navegação da Paginação
    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns([1, 1, 3, 1, 1])
    
    with col_p1:
        if st.button("⏮️ Primeira", use_container_width=True, disabled=(pag_atual == 1)):
            st.session_state["pag_hist_atual"] = 1
            st.rerun()
            
    with col_p2:
        if st.button("◀️ Anterior", use_container_width=True, disabled=(pag_atual == 1)):
            st.session_state["pag_hist_atual"] = pag_atual - 1
            st.rerun()
            
    with col_p3:
        st.markdown(
            f"<div style='text-align: center; padding-top: 5px; font-weight: 700; color: var(--text-color);'>"
            f"Página <span style='color: var(--primary-color); font-size: 1.1rem;'>{pag_atual}</span> de {total_paginas} &nbsp;|&nbsp; "
            f"<span style='font-weight: 400; color: gray;'>Total: {total_registros} registros</span>"
            f"</div>",
            unsafe_allow_html=True
        )
        
    with col_p4:
        if st.button("Próxima ▶️", use_container_width=True, disabled=(pag_atual >= total_paginas)):
            st.session_state["pag_hist_atual"] = pag_atual + 1
            st.rerun()
            
    with col_p5:
        if st.button("Última ⏭️", use_container_width=True, disabled=(pag_atual >= total_paginas)):
            st.session_state["pag_hist_atual"] = total_paginas
            st.rerun()

    # Fatia os dados apenas para a página atual (Lazy Rendering no Browser)
    start_idx = (pag_atual - 1) * itens_por_pagina
    end_idx = min(start_idx + itens_por_pagina, total_registros)
    df_pagina_slice = df_exibicao.iloc[start_idx:end_idx]

    # Exibe a tabela paginada
    st.dataframe(
        df_pagina_slice.rename(columns={
            'id': 'ID Lançamento',
            'produto': 'Insumo / Item',
            'data_hora': 'Data / Hora',
            'tipo': 'Operação',
            'quantidade': 'Qtd. Movimentada',
            'saldo_resultante': 'Saldo Resultante',
            'observacao': 'Detalhes / Motivo'
        }),
        use_container_width=True,
        hide_index=True
    )
    
    # --- OPÇÕES DE EXPORTAÇÃO COMPLETA DO HISTÓRICO ---
    st.write("")
    c_exp1, c_exp2 = st.columns([3, 1])
    with c_exp1:
        st.caption(f"Mostrando registros {start_idx + 1} a {end_idx} de um total de {total_registros}.")
    with c_exp2:
        excel_hist_data = gerar_excel_movimentacoes(mv_filtrado.drop(columns=['dt']))
        st.download_button(
            label="📊 Exportar Histórico (Excel)",
            data=excel_hist_data,
            file_name=f"Historico_Movimentacoes_WMS_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )
