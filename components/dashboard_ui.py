import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from database.connection import get_conn

def render_dashboard_ui(df):
    if df.empty:
        st.info("📦 **Bem-vindo ao WMS 5.0!** Atualmente não existem insumos cadastrados no inventário. Para começar, acesse a aba **⚙️ Config** e realize o cadastro dos seus produtos.")
        return

    # Cálculos Logísticos
    df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]
    
    with get_conn() as conn:
        cons = pd.read_sql("""
            SELECT id_produto, SUM(ABS(quantidade)) as total 
            FROM movimentacoes 
            WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0)
            GROUP BY id_produto
        """, conn)
        
    df = df.merge(cons, left_on='id', right_on='id_produto', how='left').fillna(0)
    df['consumo_diario'] = df['total'] / 30
    
    mask = df['consumo_diario'] > 0
    df['Runway'] = 999
    df.loc[mask, 'Runway'] = (df.loc[mask, 'saldo_atual'] / df.loc[mask, 'consumo_diario']).astype(int)
    
    def set_status(row):
        if row['saldo_atual'] <= 0: return '🔴 Ruptura'
        if row['saldo_atual'] < row['estoque_minimo']: return '🔴 Crítico'
        if row['Runway'] != 999 and row['Runway'] <= row['lead_time']: return '🟠 Risco'
        return '🟢 OK'
        
    df['Status'] = df.apply(set_status, axis=1)
    df['Runway_Txt'] = df['Runway'].apply(lambda x: "Sem consumo" if x == 999 else f"{x} dias")

    itens_criticos = int((df["saldo_atual"] < df["estoque_minimo"]).sum())
    if itens_criticos > 0:
        card_critico_style = 'background-color: rgba(239, 68, 68, 0.15); border-top: 4px solid #ef4444; color: #ef4444;'
    else:
        card_critico_style = 'background-color: rgba(16, 185, 129, 0.15); border-top: 4px solid #10b859; color: #10b859;'

    # Cartões de Métricas
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    c1.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Categorias<br><b>{df["categoria"].nunique()}</b></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Valor Total<br><b>R$ {df["valor_total"].sum():,.2f}</b></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="metric-card" style="{card_critico_style}">Itens Críticos/Ruptura<br><b>{itens_criticos}</b></div>', unsafe_allow_html=True)
    c4.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Giro Total<br><b>{int(df["total"].sum())} un</b></div>', unsafe_allow_html=True)

    st.divider()
    
    # Filtros Operacionais
    cp1, cp2 = st.columns([1, 1])
    with cp1:
        setores = ["Todos"] + list(df["categoria"].unique())
        setor_sel = st.selectbox("⚡ Filtrar por Setor:", setores)
    with cp2:
        busca_nome = st.text_input("🔍 Busca Rápida por Nome do Insumo:")
    
    df_filtrado = df.copy()
    if setor_sel != "Todos":
        df_filtrado = df_filtrado[df_filtrado["categoria"] == setor_sel]
    if busca_nome.strip():
        df_filtrado = df_filtrado[df_filtrado["nome"].str.contains(busca_nome, case=False)]

    st.subheader("📋 Posição de Estoque")
    
    def destacar_status(val):
        if '🔴' in str(val): return 'background-color: rgba(239, 68, 68, 0.35); color: #000000; font-weight: bold;'
        if '🟠' in str(val): return 'background-color: rgba(245, 158, 11, 0.35); color: #000000; font-weight: bold;'
        if '🟢' in str(val): return 'background-color: rgba(16, 185, 129, 0.35); color: #000000; font-weight: bold;'
        return ''

    display_df = df_filtrado[['Status', 'categoria', 'nome', 'saldo_atual', 'valor_unitario', 'estoque_minimo', 'Runway_Txt']].rename(
        columns={'categoria':'Setor', 'nome':'Produto', 'valor_unitario': 'Preço Médio', 'Runway_Txt':'Cobertura (Runway)'}
    )
    
    st.dataframe(
        display_df.style.map(destacar_status, subset=['Status']).format({'Preço Médio': 'R$ {:.2f}'}),
        hide_index=True, width='stretch'
    )

    st.divider()
    
    # Gráficos de Performance e Giro
    st.subheader("📊 Gráficos de Performance e Movimentação")
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("##### 📊 Giro Total (Saídas) por Categoria")
        giro_setor = df.groupby("categoria")["total"].sum().reset_index().rename(columns={"categoria": "Setor", "total": "Movimentações"})
        if giro_setor["Movimentações"].sum() > 0:
            fig_giro = px.bar(
                giro_setor, 
                x="Setor", 
                y="Movimentações", 
                color="Movimentações",
                text_auto=True,
                color_continuous_scale="Viridis"
            )
            fig_giro.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300, showlegend=False, coloraxis_showscale=False)
            st.plotly_chart(fig_giro, use_container_width=True)
        else:
            st.info("Ainda não há registros de saídas.")
            
    with g2:
        st.markdown("##### 🏆 Distribuição de Capital Imobilizado por Setor")
        if df["valor_total"].sum() > 0:
            valor_setor = df.groupby("categoria")["valor_total"].sum().reset_index().rename(columns={"categoria": "Setor", "valor_total": "Valor Total"})
            fig_pie = px.pie(
                valor_setor, 
                values="Valor Total", 
                names="Setor", 
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_pie.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Ainda não há produtos com saldo em estoque.")
    
    st.divider()
    
    # Matriz Scatter de Risco
    st.markdown("##### 🎯 Matriz Dinâmica de Risco: Cobertura (Runway) vs Tempo de Entrega (Lead Time)")
    df_scatter = df.copy()
    df_scatter['Runway_Scatter'] = df_scatter['Runway'].apply(lambda x: 45 if x == 999 else min(x, 45))
    
    fig_scatter = px.scatter(
        df_scatter,
        x="Runway_Scatter",
        y="lead_time",
        color="Status",
        size=df_scatter["saldo_atual"].clip(lower=8),
        hover_name="nome",
        labels={"Runway_Scatter": "Cobertura de Estoque (Dias)", "lead_time": "Tempo de Entrega (Dias)", "Status": "Criticidade"},
        color_discrete_map={"🔴 Ruptura": "#ef4444", "🔴 Crítico": "#ea580c", "🟠 Risco": "#f59e0b", "🟢 OK": "#10b859"}
    )
    
    fig_scatter.add_trace(
        go.Scatter(
            x=[0, 45],
            y=[0, 45],
            mode="lines",
            name="Limite de Ruptura (Runway = Lead Time)",
            line=dict(color="#ef4444", dash="dash", width=2),
            showlegend=True
        )
    )
    
    fig_scatter.update_layout(
        xaxis_range=[0, 48],
        yaxis_range=[0, 20],
        margin=dict(t=20, b=20, l=20, r=20),
        height=350,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

    st.divider()
    
    # Sugestões de Compra WMS
    st.subheader("🛒 Sugestão de Reposição (Cálculo WMS)")
    df_filtrado["Minimo Ideal"] = (df_filtrado["consumo_diario"] * df_filtrado["lead_time"] * 1.2).astype(int)
    df_filtrado["Alvo"] = df_filtrado[["estoque_minimo", "Minimo Ideal"]].max(axis=1)
    df_filtrado["Sugestão Compra"] = (df_filtrado["Alvo"] - df_filtrado["saldo_atual"]).clip(lower=0)
    
    apenas_compras = st.checkbox("🛒 Mostrar apenas insumos com necessidade de compra urgente")
    df_compras = df_filtrado.copy()
    if apenas_compras:
        df_compras = df_compras[df_compras["Sugestão Compra"] > 0]
        
    st.dataframe(
        df_compras[["categoria", "nome", "lead_time", "saldo_atual", "Minimo Ideal", "Sugestão Compra"]].rename(
            columns={"categoria": "Setor", "nome": "Produto", "lead_time": "Entrega(d)", "saldo_atual": "Saldo", "Sugestão Compra": "Comprar"}
        ), 
        hide_index=True, width='stretch'
    )
