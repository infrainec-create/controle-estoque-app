import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from database.connection import get_conn

def apply_premium_chart_theme(fig, is_dual_axis=False):
    layout_update = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Source Sans Pro, Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(128, 128, 128, 0.12)", zeroline=False),
        yaxis=dict(gridcolor="rgba(128, 128, 128, 0.12)", zeroline=False),
    )
    if is_dual_axis:
        layout_update["yaxis2"] = dict(
            gridcolor="rgba(128, 128, 128, 0.04)", 
            zeroline=False, 
            overlaying="y", 
            side="right"
        )
    fig.update_layout(**layout_update)
    return fig

def render_dashboard_ui(df):
    if df.empty:
        st.info("📦 **Bem-vindo ao WMS 5.0!** Atualmente não existem insumos cadastrados no inventário. Para começar, acesse a aba **⚙️ Config** e realize o cadastro dos seus produtos.")
        return

    # Carregar fatores de segurança por setor configurados no banco
    fatores_setor = {}
    padroes = {"Limpeza": 1.1, "Copa": 1.1, "EPI": 1.2, "Escritório": 1.1, "Geral": 1.1}
    with get_conn() as conn:
        rows_f = conn.execute("SELECT chave, valor FROM configuracoes WHERE chave LIKE 'fator_seguranca_%'").fetchall()
        for k, v in rows_f:
            setor_nome = k.replace("fator_seguranca_", "")
            fatores_setor[setor_nome] = float(v)

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
    with st.expander("⚙️ Parâmetros Logísticos Avançados (Janela de Consumo & Coberturas por Setor)", expanded=False):
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
            st.markdown("**🎯 Fatores de Segurança Ativos por Setor**")
            st.caption("Margens definidas na aba de Configurações")
            cols_f = st.columns(5)
            setores_nomes = ["Limpeza", "Copa", "EPI", "Escritório", "Geral"]
            for i, s in enumerate(setores_nomes):
                val_f = fatores_setor.get(s, padroes.get(s, 1.1))
                cols_f[i].metric(s, f"{val_f}x")

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
    
    # Cálculos Logísticos para Ponto de Pedido Automático e Estoque de Segurança
    def obter_fator_setor(row):
        cat = row["categoria"]
        return fatores_setor.get(cat, padroes.get(cat, 1.1))
        
    df["Fator_Seguranca"] = df.apply(obter_fator_setor, axis=1)
    df["Estoque_Seguranca"] = np.maximum(df["estoque_minimo"], np.ceil(df["consumo_diario"] * df["lead_time"] * df["Fator_Seguranca"]).astype(int))
    df["Consumo_Lead_Time"] = np.ceil(df["consumo_diario"] * df["lead_time"]).astype(int)
    df["Ponto_Pedido"] = df["Consumo_Lead_Time"] + df["Estoque_Seguranca"]
    
    # 5. Runway e Status
    mask = df['consumo_diario'] > 0
    df['Runway'] = 999
    df.loc[mask, 'Runway'] = (df.loc[mask, 'saldo_atual'] / df.loc[mask, 'consumo_diario']).astype(int)
    
    def set_status(row):
        if row['saldo_atual'] <= 0: return '🔴 Ruptura'
        if row['saldo_atual'] < row['estoque_minimo']: return '🔴 Crítico'
        if row['saldo_atual'] <= row['Ponto_Pedido']: return '🟠 Ponto de Pedido'
        return '🟢 OK'
        
    df['Status'] = df.apply(set_status, axis=1)
    df['Runway_Txt'] = df['Runway'].apply(lambda x: "Sem consumo" if x == 999 else f"{x} dias")

    # Cálculos agregados de Supply Chain (Giro, DIO, Ruptura)
    df['estoque_medio'] = df['saldo_atual'] + (df['total'] / 2.0)
    df.loc[df['estoque_medio'] <= 0, 'estoque_medio'] = 1.0
    
    custo_consumo_total = (df['total'] * df['valor_unitario']).sum()
    valor_estoque_medio = (df['estoque_medio'] * df['valor_unitario']).sum()
    
    giro_periodo = (custo_consumo_total / valor_estoque_medio) if valor_estoque_medio > 0 else 0.0
    giro_anualizado = giro_periodo * (365.0 / janela_dias)
    
    consumo_diario_financeiro = (df['consumo_diario'] * df['valor_unitario']).sum()
    dio_medio = (valor_estoque_medio / consumo_diario_financeiro) if consumo_diario_financeiro > 0 else 999.0
    
    total_itens = len(df)
    rupturas = (df["saldo_atual"] <= 0).sum()
    taxa_ruptura = (rupturas / total_itens * 100) if total_itens > 0 else 0.0

    # Cartões de Métricas
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    c1.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #3b82f6;">
            <div class="card-title">💰 Capital Imobilizado</div>
            <div class="card-value">R$ {df["valor_total"].sum():,.2f}</div>
        </div>
    ''', unsafe_allow_html=True)
    
    if taxa_ruptura > 10:
        ruptura_style = 'border-top: 4px solid #ef4444;'
    elif taxa_ruptura > 0:
        ruptura_style = 'border-top: 4px solid #ea580c;'
    else:
        ruptura_style = 'border-top: 4px solid #10b859;'
        
    c2.markdown(f'''
        <div class="metric-card" style="{ruptura_style}">
            <div class="card-title">🚨 Taxa de Ruptura</div>
            <div class="card-value">{taxa_ruptura:.1f}%</div>
        </div>
    ''', unsafe_allow_html=True)
    
    c3.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #8b5cf6;">
            <div class="card-title">🔄 Giro de Estoque (An.)</div>
            <div class="card-value">{giro_anualizado:.2f}x</div>
        </div>
    ''', unsafe_allow_html=True)
    
    dio_txt = "Sem saídas" if dio_medio == 999.0 else f"{dio_medio:.1f} dias"
    c4.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #ea580c;">
            <div class="card-title">📅 Cobertura Média (DIO)</div>
            <div class="card-value">{dio_txt}</div>
        </div>
    ''', unsafe_allow_html=True)

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

    df_filtrado["criticidade"] = df_filtrado["criticidade"].fillna("Y").str.upper()
    display_df = df_filtrado[['Status', 'categoria', 'nome', 'criticidade', 'saldo_atual', 'Ponto_Pedido', 'Runway_Txt']].rename(
        columns={
            'categoria':'Setor', 
            'nome':'Produto', 
            'criticidade':'Crit.', 
            'saldo_atual':'Saldo Físico', 
            'Ponto_Pedido':'Ponto Pedido', 
            'Runway_Txt':'Cobertura (Runway)'
        }
    )
    
    st.dataframe(
        display_df.style.map(destacar_status, subset=['Status']),
        hide_index=True, width='stretch'
    )

    st.divider()
    
    g_tabs = st.tabs(["📈 Distribuição & Giro", "🏆 Curva ABC (Financeiro)", "🔍 Matriz ABC-XYZ (Criticidade)", "🎯 Matriz de Risco & Lead Time"])
    
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
                apply_premium_chart_theme(fig_giro)
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
                apply_premium_chart_theme(fig_pie)
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
            apply_premium_chart_theme(fig_abc, is_dual_axis=True)
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
        st.markdown("##### 🔍 Matriz de Interseção ABC-XYZ")
        st.caption("A Matriz ABC-XYZ cruza o valor financeiro do estoque (ABC) com a criticidade operacional (XYZ): "
                   "**Classe X** (Baixa criticidade), **Classe Y** (Média criticidade) e **Classe Z** (Crítica/Vital).")
        
        # Obter os dados da Matriz ABC-XYZ
        df_matrix = df.copy()
        df_matrix["criticidade"] = df_matrix["criticidade"].fillna("Y").str.upper()
        
        # Classificação XYZ amigável
        xyz_labels = {"X": "X (Baixa)", "Y": "Y (Média)", "Z": "Z (Crítica/Vital)"}
        df_matrix["Classe_XYZ"] = df_matrix["criticidade"].map(xyz_labels).fillna("Y (Média)")
        
        # Criar a matriz de contagem 3x3
        matrix_counts = pd.DataFrame(
            0,
            index=["Classe A", "Classe B", "Classe C"],
            columns=["X (Baixa)", "Y (Média)", "Z (Crítica/Vital)"]
        )
        
        for _, row in df_matrix.iterrows():
            abc = row["Classe_ABC"]
            xyz = row["Classe_XYZ"]
            if abc in matrix_counts.index and xyz in matrix_counts.columns:
                matrix_counts.loc[abc, xyz] += 1
                
        # Exibir o gráfico Heatmap interativo
        fig_matrix = px.imshow(
            matrix_counts.values,
            labels=dict(x="Criticidade (XYZ)", y="Impacto Financeiro (ABC)", color="Insumos"),
            x=matrix_counts.columns,
            y=matrix_counts.index,
            text_auto=True,
            color_continuous_scale="Reds"
        )
        fig_matrix.update_layout(
            margin=dict(t=10, b=10, l=10, r=10),
            height=300,
            coloraxis_showscale=False
        )
        apply_premium_chart_theme(fig_matrix)
        st.plotly_chart(fig_matrix, use_container_width=True)
        
        # Recomendações Estratégicas para cada interseção
        st.markdown("##### 💡 Recomendações Logísticas de Compra:")
        r_cols = st.columns(3)
        with r_cols[0]:
            st.error("**🔴 Quadrante Crítico (A-Z / B-Z)**")
            st.markdown(
                "- **Foco:** Máxima segurança contra rupturas.\n"
                "- **Ação:** Manter estoque de segurança alto, auditorias frequentes e contratos com fornecedores confiáveis."
            )
        with r_cols[1]:
            st.warning("**🟡 Quadrante de Atenção (A-X / A-Y / B-Y)**")
            st.markdown(
                "- **Foco:** Otimização financeira de capital.\n"
                "- **Ação:** Reduzir estoque de segurança (itens de fácil substituição) para liberar capital de giro imobilizado."
            )
        with r_cols[2]:
            st.success("**🟢 Quadrante Simplificado (C-X / C-Y / C-Z)**")
            st.markdown(
                "- **Foco:** Eficiência operacional (custo de pedido).\n"
                "- **Ação:** Comprar lotes maiores (alta cobertura de estoque) para reduzir a frequência de novas solicitações."
            )
            
    with g_tabs[3]:
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
            color_discrete_map={"🔴 Ruptura": "#ef4444", "🔴 Crítico": "#ea580c", "🟠 Ponto de Pedido": "#f59e0b", "🟢 OK": "#10b859"}
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
        apply_premium_chart_theme(fig_scatter)
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
            apply_premium_chart_theme(fig_lead)
            st.plotly_chart(fig_lead, use_container_width=True)
        else:
            st.info("Cadastre lead times válidos nos produtos para visualizar as médias por setor.")
            
    st.divider()
    
    # Sugestões de Compra WMS
    st.subheader("🛒 Sugestão de Reposição (Cálculo WMS)")
    
    # Aplica o fator de segurança dinâmico com base na classe ABC configurada
    def obter_fator_setor(row):
        cat = row["categoria"]
        return fatores_setor.get(cat, padroes.get(cat, 1.1))
        
    df_filtrado["Fator_Seguranca"] = df_filtrado.apply(obter_fator_setor, axis=1)
    
    # O Mínimo Ideal é o estoque de segurança
    minimo_calculado = np.ceil(df_filtrado["consumo_diario"] * df_filtrado["lead_time"] * df_filtrado["Fator_Seguranca"]).astype(int)
    df_filtrado["Minimo Ideal"] = np.maximum(df_filtrado["estoque_minimo"], minimo_calculado)
    
    # Ponto de Pedido = Consumo no Lead Time + Mínimo Ideal (Estoque de Segurança)
    df_filtrado["Consumo_LT"] = np.ceil(df_filtrado["consumo_diario"] * df_filtrado["lead_time"]).astype(int)
    df_filtrado["Ponto_Pedido"] = df_filtrado["Consumo_LT"] + df_filtrado["Minimo Ideal"]
    
    # A sugestão de compra é recomendada se Saldo <= Ponto de Pedido
    df_filtrado["Sugestão Compra"] = 0
    sub_pp = df_filtrado["saldo_atual"] <= df_filtrado["Ponto_Pedido"]
    df_filtrado.loc[sub_pp, "Sugestão Compra"] = np.ceil(df_filtrado.loc[sub_pp, "Ponto_Pedido"] * 1.5 - df_filtrado.loc[sub_pp, "saldo_atual"]).astype(int).clip(lower=0)
    
    # Previsão de entrega baseada nas regras operacionais
    from utils.date_helpers import calcular_previsao_entrega
    crono_entrega = calcular_previsao_entrega()
    data_entrega_str = crono_entrega["data_entrega"].strftime("%d/%m/%Y")
    
    df_filtrado["Previsão de Entrega"] = df_filtrado.apply(
        lambda r: data_entrega_str if r["Sugestão Compra"] > 0 else "Estoque OK",
        axis=1
    )
    
    apenas_compras = st.checkbox("🛒 Mostrar apenas insumos com necessidade de compra urgente")
    df_compras = df_filtrado.copy()
    if apenas_compras:
        df_compras = df_compras[df_compras["Sugestão Compra"] > 0]
        
    df_compras["criticidade"] = df_compras["criticidade"].fillna("Y").str.upper()
    
    st.dataframe(
        df_compras[["categoria", "nome", "criticidade", "saldo_atual", "Ponto_Pedido", "Minimo Ideal", "Sugestão Compra", "Previsão de Entrega"]].rename(
            columns={
                "categoria": "Setor", 
                "nome": "Produto", 
                "criticidade": "Crit.",
                "saldo_atual": "Saldo Físico", 
                "Ponto_Pedido": "Ponto de Pedido",
                "Minimo Ideal": "Est. Segurança", 
                "Sugestão Compra": "Sugestão Compra",
                "Previsão de Entrega": "Previsão Entrega"
            }
        ), 
        hide_index=True, width='stretch'
    )
