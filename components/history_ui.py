import pandas as pd
import streamlit as st

def render_history_ui(df, mv):
    st.subheader("📜 Histórico de Movimentações")
    if mv.empty:
        st.info("Nenhuma movimentação registrada no histórico.")
        return

    # 1. Parsing robusto de datas para filtragem e ordenação
    mv['dt'] = pd.to_datetime(mv['data_hora'], format='%d/%m/%Y %H:%M', errors='coerce')
    mask_nat = mv['dt'].isna()
    if mask_nat.any():
        mv.loc[mask_nat, 'dt'] = pd.to_datetime(mv.loc[mask_nat, 'data_hora'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
        
    # --- FILTRO TEMPORAL GLOBAL DA ABA ---
    st.markdown("##### 🔍 Filtro Temporal de Histórico")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        opcao_tempo = st.selectbox(
            "Selecione o Intervalo de Visualização:",
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
                "Escolha as datas inicial e final:",
                value=(hoje.date() - pd.Timedelta(days=30), hoje.date()),
                key="range_historico"
            )
            if isinstance(data_range, tuple) and len(data_range) == 2:
                data_inicio = pd.Timestamp(data_range[0])
                data_fim = pd.Timestamp(data_range[1])
            else:
                data_inicio = hoje - pd.Timedelta(days=30)
                data_fim = hoje
                
    # Aplica o filtro de data
    mv_filtrado = mv[(mv['dt'] >= data_inicio) & (mv['dt'] <= data_fim + pd.Timedelta(days=1))].copy()
    
    if mv_filtrado.empty:
        st.warning("⚠️ Nenhuma movimentação registrada no período selecionado.")
        return

    # --- 2. GRÁFICO COMPARATIVO DE CONSUMO (SAÍDAS) ---
    st.divider()
    st.markdown("##### 📊 Comparativo de Consumo Mensal (Saídas de Estoque)")
    st.caption("Compare o consumo de hoje com os meses anteriores.")
    
    saidas_geral = mv[mv['tipo'] == 'Saída'].copy()
    if not saidas_geral.empty:
        # Criar Mês/Ano e AnoMes ordenado
        saidas_geral['Mês/Ano'] = saidas_geral['dt'].dt.strftime('%m/%Y')
        saidas_geral['AnoMes'] = saidas_geral['dt'].dt.to_period('M')
        
        # Agrupar por AnoMes e Produto e somar (quantidade de saída é armazenada negativa, então usamos abs())
        consumo_mensal = saidas_geral.groupby(['AnoMes', 'produto'])['quantidade'].apply(lambda x: x.abs().sum()).reset_index()
        consumo_mensal['Período (Mês/Ano)'] = consumo_mensal['AnoMes'].astype(str)
        consumo_mensal.rename(columns={'quantidade': 'Consumo Total (un)'}, inplace=True)
        
        # Filtro de produto
        produtos_saida = ["Todos os Insumos"] + list(saidas_geral['produto'].unique())
        prod_sel_comp = st.selectbox("Filtrar gráfico comparativo por item:", produtos_saida, key="prod_sel_comp_hist")
        
        if prod_sel_comp != "Todos os Insumos":
            dados_graf = consumo_mensal[consumo_mensal['produto'] == prod_sel_comp]
        else:
            dados_graf = consumo_mensal.groupby('Período (Mês/Ano)')['Consumo Total (un)'].sum().reset_index()
            
        if not dados_graf.empty:
            # Ordenar por Período de forma crescente para o gráfico
            dados_graf = dados_graf.sort_values(by="Período (Mês/Ano)")
            st.bar_chart(data=dados_graf, x="Período (Mês/Ano)", y="Consumo Total (un)", color="#3b82f6")
            st.caption("💡 *O gráfico acima permite visualizar a variação de consumo (saídas) mês a mês do item selecionado.*")
        else:
            st.info("Nenhum consumo registrado para plotagem comparativa.")
    else:
        st.info("Ainda não há saídas registradas para exibir o comparativo gráfico.")

    # --- 3. EVOLUÇÃO DE PREÇOS (Usa dados filtrados) ---
    st.divider()
    st.markdown("##### 📈 Gráfico de Evolução de Preços (Entradas)")
    if not df.empty:
        item_analise = st.selectbox("Selecione o Insumo para ver a Curva de Custos:", list(df["nome"].unique()))
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
                st.info("Nenhum preço de compra detalhado foi encontrado para este produto no período filtrado.")
        else:
            st.info("Ainda não existem entradas registradas para este produto no período filtrado.")

    # --- 4. TABELA DE HISTÓRICO GERAL ---
    st.divider()
    st.markdown("##### 📋 Histórico Geral das Movimentações no Período Selecionado")
    
    # Exibe a tabela com os dados filtrados
    df_exibicao = mv_filtrado.drop(columns=['dt'])
    
    # Adicionamos busca rápida na tabela de histórico
    busca_hist = st.text_input("🔍 Busca Rápida no Histórico (Produto ou Registro/Obs):", "").strip()
    if busca_hist:
        df_exibicao = df_exibicao[
            df_exibicao['produto'].str.contains(busca_hist, case=False, na=False) |
            df_exibicao['observacao'].str.contains(busca_hist, case=False, na=False)
        ]
        
    if not df_exibicao.empty:
        st.dataframe(df_exibicao, use_container_width=True, hide_index=True)
    else:
        st.warning("Nenhuma movimentação corresponde aos critérios de filtragem do período.")
