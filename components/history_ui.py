import pandas as pd
import streamlit as st

def render_history_ui(df, mv):
    st.subheader("📜 Histórico de Movimentações")
    if mv.empty:
        st.info("Nenhuma movimentação registrada no histórico.")
        return

    st.markdown("##### 📈 Gráfico de Auditoria e Evolução de Preços")
    if not df.empty:
        item_analise = st.selectbox("Selecione o Insumo para ver a Curva de Custos:", list(df["nome"].unique()))
        entradas_item = mv[(mv["produto"] == item_analise) & (mv["tipo"] == "Entrada")].copy()
        
        if not entradas_item.empty:
            def extrair_preco(obs):
                try:
                    if "Pago: R$" in str(obs):
                        return float(str(obs).split("Pago: R$ ")[1].split("/un")[0])
                except: pass
                return None
            
            entradas_item["Preço de Compra (R$)"] = entradas_item["observacao"].apply(extrair_preco)
            entradas_item = entradas_item.dropna(subset=["Preço de Compra (R$)"]).iloc[::-1]
            if not entradas_item.empty: 
                st.line_chart(data=entradas_item, x="data_hora", y="Preço de Compra (R$)", width='stretch')
            else:
                st.info("Nenhum preço de compra detalhado foi encontrado para este produto.")
        else:
            st.info("Ainda não existem entradas registradas para este produto.")
    
    st.divider()
    st.markdown("##### 📋 Histórico Geral das Movimentações")
    mv['Mês/Ano'] = mv['data_hora'].apply(lambda x: x.split()[0][3:])
    mes_selecionado = st.selectbox("Filtrar por Período:", sorted(mv['Mês/Ano'].unique(), reverse=True))
    st.dataframe(mv[mv['Mês/Ano'] == mes_selecionado].drop(columns=['Mês/Ano']), use_container_width=True, hide_index=True)
