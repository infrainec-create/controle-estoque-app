import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from database.connection import get_conn

def render_dashboard_ui(df):
    if df.empty:
        st.info("📦 **Bem-vindo ao WMS 5.0!** Atualmente não existem insumos cadastrados no inventário. Para começar, acesse a aba **⚙️ Config** e realize o cadastro dos seus produtos.")
        return

    # 1. Cálculos de Valuation (Valor Total)
    df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]

    # 2. Classificação da Curva ABC (baseada no valor total imobilizado)
    df_abc = df.sort_values(by="valor_total", ascending=False).copy()
    total_valor = df_abc["valor_total"].sum()
    classes_map = {}
    if total_valor > 0:
        df_abc["valor_acumulado"] = df_abc["valor_total"].cumsum()
        df_abc["perc_acumulado"] = (df_abc["valor_acumulado"] / total_valor) * 100
        
        def get_class(row):
            val = row["perc_acumulado"]
            if val <= 80: return "Classe A"
            if val <= 95: return "Classe B"
            return "Classe C"
        df_abc["Classe"] = df_abc.apply(get_class, axis=1)
        classes_map = dict(zip(df_abc["id"], df_abc["Classe"]))
    else:
        classes_map = {id_prod: "Classe C" for id_prod in df["id"]}
        
    df["Classe_ABC"] = df["id"].map(classes_map).fillna("Classe C")

    # 3. Controles Logísticos Dinâmicos (Expander no topo do painel)
    with st.expander("⚙️ Parâmetros Logísticos Avançados (Janela de Consumo & Margem ABC)", expanded=False):
        col_janela, col_margens = st.columns([1, 2])
        with col_janela:
            st.markdown("**📅 Ritmo de Consumo**")
            janela_dias = st.select_slider(
                "Janela de análise de saídas:",
                options=[7, 15, 30, 90, 180],
                value=30,
                format_func=lambda x: f"{x} dias"
            )
        with col_margens:
            st.markdown("**🎯 Fatores de Segurança (Curva ABC)**")
            cm1, cm2, cm3 = st.columns(3)
            fator_a = cm1.number_input(
                "Classe A (Crítico)", 
                min_value=1.0, 
                max_value=2.0, 
                value=1.4, 
                step=0.1, 
                help="Fator de cobertura para itens Classe A (ex: 1.4 = 40% de margem)"
            )
            fator_b = cm2.number_input(
                "Classe B (Médio)", 
                min_value=1.0, 
                max_value=2.0, 
                value=1.2, 
                step=0.1, 
                help="Fator de cobertura para itens Classe B (ex: 1.2 = 20% de margem)"
            )
            fator_c = cm3.number_input(
                "Classe C (Baixo)", 
                min_value=1.0, 
                max_value=2.0, 
                value=1.1, 
                step=0.1, 
                help="Fator de cobertura para itens Classe C (ex: 1.1 = 10% de margem)"
            )

    # 4. Cálculo de Consumo diário baseado na janela temporal selecionada
    with get_conn() as conn:
        movs = pd.read_sql("""
            SELECT id_produto, data_hora, quantidade
            FROM movimentacoes 
            WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0)
        """, conn)
        
    cons_dict = {}
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    if not movs.empty:
        movs['dt'] = pd.to_datetime(movs['data_hora'], format='%d/%m/%Y %H:%M', errors='coerce')
        agora = datetime.now(ZoneInfo("America/Fortaleza")).replace(tzinfo=None)
        limite = agora - pd.Timedelta(days=janela_dias)
        movs_filtradas = movs[movs['dt'] >= limite]
        
        if not movs_filtradas.empty:
            cons = movs_filtradas.groupby('id_produto')['quantidade'].apply(lambda x: x.abs().sum()).reset_index(name='total')
            cons_dict = dict(zip(cons['id_produto'], cons['total']))
            
    df['total'] = df['id'].map(cons_dict).fillna(0)
    df['consumo_diario'] = df['total'] / janela_dias
    
    # 5. Runway e Status
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
    c4.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Giro ({janela_dias}d)<br><b>{int(df["total"].sum())} un</b></div>', unsafe_allow_html=True)

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
    
    g_tabs = st.tabs(["📈 Distribuição & Giro", "🏆 Curva ABC (Financeiro)", "🎯 Matriz de Risco & Lead Time"])
    
    with g_tabs[0]:
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
                
    with g_tabs[1]:
        st.markdown("##### 🏆 Análise de Parede da Curva ABC")
        st.caption("A Curva ABC classifica seus insumos pelo valor imobilizado acumulado: Classe A (80% do valor total), Classe B (próximos 15%) e Classe C (restante 5%).")
        
        # Calcular Curva ABC
        df_abc = df.sort_values(by="valor_total", ascending=False).copy()
        total_valor = df_abc["valor_total"].sum()
        if total_valor > 0:
            df_abc["valor_acumulado"] = df_abc["valor_total"].cumsum()
            df_abc["perc_acumulado"] = (df_abc["valor_acumulado"] / total_valor) * 100
            
            def get_class(row):
                val = row["perc_acumulado"]
                if val <= 80: return "Classe A"
                if val <= 95: return "Classe B"
                return "Classe C"
            df_abc["Classe"] = df_abc.apply(get_class, axis=1)
            
            # Gráfico de Pareto / Acumulado
            fig_abc = go.Figure()
            colors_map = {"Classe A": "#ef4444", "Classe B": "#f59e0b", "Classe C": "#10b859"}
            bar_colors = df_abc["Classe"].map(colors_map).tolist()
            
            fig_abc.add_trace(go.Bar(
                x=df_abc["nome"],
                y=df_abc["valor_total"],
                name="Valor Imobilizado",
                marker_color=bar_colors,
                hovertemplate="<b>%{x}</b><br>Valor: R$ %{y:,.2f}<br><extra></extra>"
            ))
            
            fig_abc.add_trace(go.Scatter(
                x=df_abc["nome"],
                y=df_abc["perc_acumulado"],
                name="% Acumulado",
                yaxis="y2",
                line=dict(color="#3b82f6", width=3),
                hovertemplate="<b>%{x}</b><br>Acumulado: %{y:.1f}%<br><extra></extra>"
            ))
            
            fig_abc.update_layout(
                yaxis=dict(title="Valor Imobilizado (R$)"),
                yaxis2=dict(title="Percentual Acumulado (%)", overlaying="y", side="right", range=[0, 105]),
                margin=dict(t=30, b=30, l=10, r=10),
                height=350,
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_abc, use_container_width=True)
            
            # Métricas rápidas por Classe ABC
            col_a, col_b, col_c = st.columns(3)
            sum_a = df_abc[df_abc["Classe"] == "Classe A"]["valor_total"].sum()
            sum_b = df_abc[df_abc["Classe"] == "Classe B"]["valor_total"].sum()
            sum_c = df_abc[df_abc["Classe"] == "Classe C"]["valor_total"].sum()
            
            col_a.metric("🔴 Classe A (Giro Crítico)", f"R$ {sum_a:,.2f}", f"{(sum_a/total_valor)*100:.1f}% do capital")
            col_b.metric("🟡 Classe B (Intermediário)", f"R$ {sum_b:,.2f}", f"{(sum_b/total_valor)*100:.1f}% do capital")
            col_c.metric("🟢 Classe C (Giro Comum)", f"R$ {sum_c:,.2f}", f"{(sum_c/total_valor)*100:.1f}% do capital")
        else:
            st.info("Cadastre valores unitários e saldos maiores que zero para ver a análise da Curva ABC.")
            
    with g_tabs[2]:
        st.markdown("##### 🎯 Matriz Dinâmica de Risco: Cobertura (Runway) vs Tempo de Entrega (Lead Time)")
        
        # Matriz Scatter de Risco
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
            height=320,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        # Tempo de Entrega médio por setor
        st.markdown("##### 🚚 Tempo de Entrega (Lead Time) Médio dos Insumos por Setor")
        df_lead = df.groupby("categoria")["lead_time"].mean().reset_index().rename(columns={"categoria": "Setor", "lead_time": "Lead Time Médio"})
        if not df_lead.empty and df_lead["Lead Time Médio"].sum() > 0:
            fig_lead = px.bar(
                df_lead,
                x="Setor",
                y="Lead Time Médio",
                color="Lead Time Médio",
                text_auto=".1f",
                color_continuous_scale="Reds",
                labels={"Lead Time Médio": "Lead Time Médio (Dias)"}
            )
            fig_lead.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280, coloraxis_showscale=False)
            st.plotly_chart(fig_lead, use_container_width=True)
        else:
            st.info("Cadastre lead times válidos nos produtos para visualizar as médias por setor.")
            
    st.divider()
    
    # Sugestões de Compra WMS
    st.subheader("🛒 Sugestão de Reposição (Cálculo WMS)")
    
    # Aplica o fator de segurança dinâmico com base na classe ABC configurada
    def obter_fator(row):
        classe = row["Classe_ABC"]
        if classe == "Classe A": return fator_a
        if classe == "Classe B": return fator_b
        return fator_c
        
    df_filtrado["Fator_Seguranca"] = df_filtrado.apply(obter_fator, axis=1)
    
    # O Mínimo Ideal é o teto do consumo do Lead Time com a margem da classe ABC, garantindo que não seja inferior ao estoque mínimo configurado
    minimo_calculado = np.ceil(df_filtrado["consumo_diario"] * df_filtrado["lead_time"] * df_filtrado["Fator_Seguranca"]).astype(int)
    df_filtrado["Minimo Ideal"] = np.maximum(df_filtrado["estoque_minimo"], minimo_calculado)
    df_filtrado["Alvo"] = df_filtrado["Minimo Ideal"]
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
