from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from database.connection import get_conn
from utils.consumption import processar_consumo_produtos, calcular_previsao_demanda_preditiva, calcular_custo_posse_estoque

def apply_premium_chart_theme(fig, is_dual_axis=False):
    layout_update = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Source Sans Pro, Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(128, 128, 128, 0.12)", zeroline=False),
        yaxis=dict(gridcolor="rgba(128, 128, 128, 0.12)", zeroline=False),
        margin=dict(l=20, r=20, t=35, b=20),
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

    # Garantir compatibilidade se a coluna de criticidade não existir no dataframe carregado/cacheado
    if "criticidade" not in df.columns:
        df["criticidade"] = "Y"

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

    # ─────────────────────────────────────────────────────────────
    # BARRA SUPERIOR: MODO DE VISUALIZAÇÃO & CONTROLES LOGÍSTICOS
    # ─────────────────────────────────────────────────────────────
    col_mode, col_blank = st.columns([2.8, 1])
    with col_mode:
        modo_visao = st.radio(
            "👁️ Modo de Visualização do Painel:",
            ["💼 Visão Executiva (Financeira)", "🔧 Visão Operacional (Compras & Almoxarifado)", "📊 Visão Completa"],
            index=2,
            horizontal=True,
            help="Alterne entre focar em métricas financeiras/estratégicas ou na operação diária de reposição e estoque."
        )

    with st.expander("⚙️ Parâmetros Logísticos Avançados (Janela de Consumo & Coberturas por Setor)", expanded=False):
        col_janela, col_metodo, col_margens = st.columns([1, 1.2, 1.8])
        with col_janela:
            st.markdown("**📅 Ritmo de Consumo**")
            janela_dias = st.select_slider(
                "Janela de análise de saídas:",
                options=[7, 15, 30, 90, 180],
                value=30,
                format_func=lambda x: f"{x} dias"
            )
        with col_metodo:
            st.markdown("**📋 Método de Cálculo**")
            metodo_consumo_lbl = st.radio(
                "Origem do consumo:",
                options=["Saídas Registradas", "Inventário (Diferenças)"],
                index=0,
                help="Saídas Registradas: soma as Saídas manuais e divergências de perda.\nInventário (Diferenças): calcula o consumo pela variação do saldo físico entre contagens semanais."
            )
            metodo_consumo = "movimentacoes" if metodo_consumo_lbl == "Saídas Registradas" else "inventario"
            st.session_state["metodo_consumo"] = metodo_consumo
        with col_margens:
            st.markdown("**🎯 Fatores de Segurança Ativos por Setor**")
            st.caption("Margens definidas na aba de Configurações")
            cols_f = st.columns(5)
            setores_nomes = ["Limpeza", "Copa", "EPI", "Escritório", "Geral"]
            for i, s in enumerate(setores_nomes):
                val_f = fatores_setor.get(s, padroes.get(s, 1.1))
                cols_f[i].metric(s, f"{val_f}x")

    # 4. Cálculo de Consumo diário baseado na janela temporal e método selecionados
    df = processar_consumo_produtos(df, metodo_consumo, janela_dias)
    
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

    df['estoque_medio'] = df['saldo_atual'] + (df['total'] / 2.0)
    df.loc[df['estoque_medio'] <= 0, 'estoque_medio'] = 1.0
    custo_consumo_total = (df['total'] * df['valor_unitario']).sum()
    valor_estoque_medio = (df['estoque_medio'] * df['valor_unitario']).sum()
    giro_periodo = (custo_consumo_total / valor_estoque_medio) if valor_estoque_medio > 0 else 0.0
    giro_anualizado = giro_periodo * (365.0 / janela_dias)
    consumo_diario_financeiro = (df['consumo_diario'] * df['valor_unitario']).sum()
    dio_medio = (valor_estoque_medio / consumo_diario_financeiro) if consumo_diario_financeiro > 0 else 999.0
    
    total_itens = len(df)
    n_ruptura = (df["saldo_atual"] <= 0).sum()
    n_critico = ((df["saldo_atual"] > 0) & (df["saldo_atual"] < df["estoque_minimo"])).sum()
    n_ponto_ped = ((df["saldo_atual"] >= df["estoque_minimo"]) & (df["saldo_atual"] <= df["Ponto_Pedido"])).sum()
    n_ok = (df["saldo_atual"] > df["Ponto_Pedido"]).sum()
    taxa_ruptura = (n_ruptura / total_itens * 100) if total_itens > 0 else 0.0

    # ─── CÁLCULO DE SUGESTÃO DE COMPRA & PREVISÃO DE ENTREGA (UNIFICADO) ───
    df["Minimo_Ideal"] = np.maximum(df["estoque_minimo"], np.ceil(df["consumo_diario"] * df["lead_time"] * df["Fator_Seguranca"]).astype(int))
    df["Sugestão Compra"] = 0
    sub_pp = df["saldo_atual"] <= df["Ponto_Pedido"]
    df.loc[sub_pp, "Sugestão Compra"] = np.ceil(df.loc[sub_pp, "Ponto_Pedido"] * 1.5 - df.loc[sub_pp, "saldo_atual"]).astype(int).clip(lower=0)
    
    from utils.date_helpers import calcular_previsao_entrega
    crono_entrega = calcular_previsao_entrega()
    data_entrega_str = crono_entrega["data_entrega"].strftime("%d/%m/%Y")
    df["Previsão de Entrega"] = df.apply(lambda r: data_entrega_str if r["Sugestão Compra"] > 0 else "Estoque OK", axis=1)

    # ─── 1. HEADER EXECUTIVO 2-EM-1 (HEALTH SCORE & MÉTRICAS PRINCIPAIS) ───
    p1_atendimento = ((total_itens - (n_ruptura + n_critico)) / total_itens * 100.0) if total_itens > 0 else 100.0
    ira_percent = 100.0
    try:
        with get_conn() as conn:
            cnt_row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN quantidade = 0 THEN 1 ELSE 0 END) FROM movimentacoes WHERE tipo = 'Contagem'").fetchone()
            if cnt_row and cnt_row[0] > 0: ira_percent = (cnt_row[1] / cnt_row[0]) * 100.0
    except Exception: pass
    p2_acuracidade = ira_percent
    valor_total_est = df["valor_total"].sum()
    valor_em_giro = df[df["consumo_diario"] > 0]["valor_total"].sum()
    p3_eficiencia = (valor_em_giro / valor_total_est * 100.0) if valor_total_est > 0 else 100.0
    health_score = round((p1_atendimento * 0.40) + (p2_acuracidade * 0.30) + (p3_eficiencia * 0.30), 1)
    
    if health_score >= 85: badge_health, color_health = "🟢 Excelente Saúde de Estoque", "#10b859"
    elif health_score >= 70: badge_health, color_health = "🟡 Estado de Alerta Moderado", "#f59e0b"
    else: badge_health, color_health = "🔴 Estado Crítico Requer Atenção", "#ef4444"
        
    st.markdown(f"""
        <div style="background: linear-gradient(135deg, rgba(0, 114, 255, 0.05) 0%, rgba(0, 198, 255, 0.02) 100%); border: 1px solid rgba(0, 114, 255, 0.18); border-radius: 16px; padding: 18px 24px; margin-bottom: 15px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;">
                <div>
                    <span style="font-size: 0.75rem; font-weight: 700; text-transform: uppercase; color: #6b7280; letter-spacing: 1px;">🏥 Painel de Comando WMS</span>
                    <h3 style="margin: 4px 0 0 0; font-size: 1.45rem; font-weight: 800; color: var(--text-color);">{badge_health}</h3>
                    <p style="margin: 4px 0 0 0; font-size: 0.85rem; color: gray;">Nível de Atendimento: {p1_atendimento:.1f}% &nbsp;|&nbsp; Acuracidade (IRA): {p2_acuracidade:.1f}% &nbsp;|&nbsp; Giro do Capital: {p3_eficiencia:.1f}%</p>
                </div>
                <div style="text-align: center; background-color: rgba(0,0,0,0.05); padding: 8px 20px; border-radius: 12px; border: 1px solid {color_health};">
                    <span style="font-size: 0.70rem; text-transform: uppercase; color: gray; font-weight: 700;">Health Score</span>
                    <div style="font-size: 2.1rem; font-weight: 800; color: {color_health}; line-height: 1;">{health_score}%</div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # ─── CARDS DE METRICAS COM DELTAS & INDICADORES DE TENDENCIA (OPÇÃO 1) ───
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    
    # Delta Capital Imobilizado
    pct_giro = (valor_em_giro / valor_total_est * 100.0) if valor_total_est > 0 else 0.0
    c1.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #3b82f6;">
            <div class="card-title">💰 Capital Imobilizado</div>
            <div class="card-value">R$ {valor_total_est:,.2f}</div>
            <div style="font-size: 0.78rem; font-weight: 600; color: #10b859; margin-top: 6px; display: flex; align-items: center; gap: 4px;">
                <span>▲ {pct_giro:.1f}%</span> em giro ativo ({janela_dias}d)
            </div>
        </div>
    ''', unsafe_allow_html=True)
    
    # Delta Taxa de Ruptura
    ruptura_style = 'border-top: 4px solid #ef4444;' if taxa_ruptura > 10 else ('border-top: 4px solid #ea580c;' if taxa_ruptura > 0 else 'border-top: 4px solid #10b859;')
    delta_ruptura_txt = f"🔴 {n_ruptura} itens zerados" if n_ruptura > 0 else "🟢 100% de disponibilidade"
    delta_ruptura_color = "#ef4444" if n_ruptura > 0 else "#10b859"
    c2.markdown(f'''
        <div class="metric-card" style="{ruptura_style}">
            <div class="card-title">🚨 Taxa de Ruptura</div>
            <div class="card-value">{taxa_ruptura:.1f}%</div>
            <div style="font-size: 0.78rem; font-weight: 600; color: {delta_ruptura_color}; margin-top: 6px;">
                {delta_ruptura_txt} (Meta: 0%)
            </div>
        </div>
    ''', unsafe_allow_html=True)
    
    # Delta Giro de Estoque
    c3.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #8b5cf6;">
            <div class="card-title">🔄 Giro de Estoque (An.)</div>
            <div class="card-value">{giro_anualizado:.2f}x</div>
            <div style="font-size: 0.78rem; font-weight: 600; color: #8b5cf6; margin-top: 6px;">
                ⚡ Projeção Anualizada ({janela_dias}d)
            </div>
        </div>
    ''', unsafe_allow_html=True)
    
    # Delta Cobertura DIO
    dio_txt = "Sem saídas" if dio_medio == 999.0 else f"{dio_medio:.1f} dias"
    c4.markdown(f'''
        <div class="metric-card" style="border-top: 4px solid #ea580c;">
            <div class="card-title">📅 Cobertura Média (DIO)</div>
            <div class="card-value">{dio_txt}</div>
            <div style="font-size: 0.78rem; font-weight: 600; color: #ea580c; margin-top: 6px;">
                🎯 Cobertura Ideal: 15 a 30 dias
            </div>
        </div>
    ''', unsafe_allow_html=True)

    # ─── DESTAQUE GRÁFICO VISÍVEL SEM EXPANDER (OPÇÃO 1) ───
    if modo_visao in ["💼 Visão Executiva (Financeira)", "📊 Visão Completa"]:
        st.markdown("##### 📈 Destaques Visuais de Saúde & Valuation")
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            fig_health_setor = px.histogram(
                df, x="categoria", color="Status", barmode="stack",
                title="📦 Distribuição de Status por Setor",
                color_discrete_map={
                    '🔴 Ruptura': '#ef4444',
                    '🔴 Crítico': '#dc2626',
                    '🟠 Ponto de Pedido': '#f59e0b',
                    '🟢 OK': '#10b859'
                },
                labels={"categoria": "Setor", "count": "Qtd Insumos"}
            )
            apply_premium_chart_theme(fig_health_setor)
            st.plotly_chart(fig_health_setor, use_container_width=True)

        with col_g2:
            top5_df = df.sort_values(by="valor_total", ascending=False).head(5)
            fig_top5 = px.bar(
                top5_df, y="nome", x="valor_total", orientation="h",
                title="💰 Top 5 Maior Valuation (R$)",
                text_auto=".2f", color="valor_total", color_continuous_scale="Blues",
                labels={"nome": "Produto / Insumo", "valor_total": "Valuation (R$)"}
            )
            fig_top5.update_layout(yaxis=dict(autorange="reversed"))
            apply_premium_chart_theme(fig_top5)
            st.plotly_chart(fig_top5, use_container_width=True)

    # ─── TIMELINE PREDITIVA DE RUPTURA & ESTOQUE OBSOLETO (OPÇÃO 2) ───
    if modo_visao in ["🔧 Visão Operacional (Compras & Almoxarifado)", "📊 Visão Completa"]:
        st.divider()
        df_pred = calcular_previsao_demanda_preditiva(df, metodo=metodo_consumo, janela_dias=janela_dias)
        
        # Filtros de esgotamento preditivo
        e_7 = df_pred[(df_pred["saldo_atual"] > 0) & (df_pred["runway_dias"] <= 7)]
        e_14 = df_pred[(df_pred["saldo_atual"] > 0) & (df_pred["runway_dias"] > 7) & (df_pred["runway_dias"] <= 14)]
        e_30 = df_pred[(df_pred["saldo_atual"] > 0) & (df_pred["runway_dias"] > 14) & (df_pred["runway_dias"] <= 30)]

        st.markdown("### ⏱️ Timeline Preditiva de Ruptura (Horizonte de Esgotamento)")
        t_col1, t_col2, t_col3 = st.columns(3)
        with t_col1:
            st.markdown(f"""
                <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 12px; padding: 14px 18px;">
                    <div style="font-size: 0.75rem; font-weight: 700; color: #ef4444; text-transform: uppercase;">🚨 Esta Semana (≤ 7 dias)</div>
                    <div style="font-size: 1.6rem; font-weight: 800; color: #ef4444; margin-top: 4px;">{len(e_7)} Insumos</div>
                    <div style="font-size: 0.78rem; color: gray; margin-top: 4px;">Risco imediato de paralisação</div>
                </div>
            """, unsafe_allow_html=True)
        with t_col2:
            st.markdown(f"""
                <div style="background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 12px; padding: 14px 18px;">
                    <div style="font-size: 0.75rem; font-weight: 700; color: #f59e0b; text-transform: uppercase;">🟠 Próxima Semana (8 a 14 dias)</div>
                    <div style="font-size: 1.6rem; font-weight: 800; color: #f59e0b; margin-top: 4px;">{len(e_14)} Insumos</div>
                    <div style="font-size: 0.78rem; color: gray; margin-top: 4px;">Disparar pedido ao fornecedor</div>
                </div>
            """, unsafe_allow_html=True)
        with t_col3:
            st.markdown(f"""
                <div style="background: rgba(59, 130, 246, 0.08); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 12px; padding: 14px 18px;">
                    <div style="font-size: 0.75rem; font-weight: 700; color: #3b82f6; text-transform: uppercase;">🟡 Próximos 30 dias (15 a 30 dias)</div>
                    <div style="font-size: 1.6rem; font-weight: 800; color: #3b82f6; margin-top: 4px;">{len(e_30)} Insumos</div>
                    <div style="font-size: 0.78rem; color: gray; margin-top: 4px;">Planejamento mensal de estoque</div>
                </div>
            """, unsafe_allow_html=True)

        if len(e_7) + len(e_14) + len(e_30) > 0:
            with st.expander("📅 Detalhamento dos Insumos com Data Estimada de Ruptura", expanded=len(e_7) > 0):
                df_timeline = pd.concat([e_7, e_14, e_30])[['nome', 'categoria', 'saldo_atual', 'runway_dias', 'data_esgotamento', 'status_cobertura']].rename(
                    columns={
                        'nome': 'Produto / Insumo', 
                        'categoria': 'Setor', 
                        'saldo_atual': 'Saldo Físico', 
                        'runway_dias': 'Dias Restantes', 
                        'data_esgotamento': 'Data Estimada Esgotamento', 
                        'status_cobertura': 'Status Cobertura'
                    }
                )
                st.dataframe(df_timeline, hide_index=True, use_container_width=True)

        # Banner de Alerta de Estoque Obsoleto (Dead Stock)
        info_posse = calcular_custo_posse_estoque(df)
        if info_posse.get('valor_capital_parado', 0) > 0:
            st.write("")
            st.markdown(f"""
                <div style="background: linear-gradient(135deg, rgba(239, 68, 68, 0.06) 0%, rgba(245, 158, 11, 0.04) 100%); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 14px; padding: 16px 20px; margin-top: 10px; margin-bottom: 10px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                        <div>
                            <strong style="color: #ef4444; font-size: 0.95rem;">🛑 Alerta de Estoque Obsoleto / Parado (Dead Stock)</strong>
                            <p style="margin: 4px 0 0 0; font-size: 0.88rem; color: var(--text-color);">
                                Existem <b>{info_posse['total_itens_parados']} insumos</b> sem nenhuma saída registrada nos últimos {janela_dias} dias, totalizando <b>R$ {info_posse['valor_capital_parado']:,.2f}</b> de capital imobilizado.
                            </p>
                        </div>
                        <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 10px; padding: 6px 14px; text-align: right;">
                            <span style="font-size: 0.70rem; text-transform: uppercase; color: gray; font-weight: 700;">Custo Carregamento Mensal</span>
                            <div style="font-size: 1.1rem; font-weight: 800; color: #ef4444;">R$ {info_posse['custo_parado_mensal']:,.2f}/mês</div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

    # ─── CENTRAL DE AÇÕES RECOMENDADAS DO DIA ───
    with st.expander("🚨 Central de Ações Recomendadas do Dia", expanded=(n_ruptura + n_critico > 0)):
        ac_c1, ac_c2, ac_c3 = st.columns(3)
        with ac_c1:
            st.markdown(f"**🛒 Pedidos Urgentes ({n_ruptura + n_critico + n_ponto_ped})**")
            if n_ruptura + n_critico > 0: st.error(f"⚠️ **{n_ruptura} Ruptura** e **{n_critico} Críticos**.")
            elif n_ponto_ped > 0: st.warning(f"🟠 **{n_ponto_ped}** no Ponto de Pedido.")
            else: st.success("🟢 Tudo OK.")
        with ac_c2:
            st.markdown("**💎 Maior Valor em Risco**")
            df_risco = df[df["Status"].isin(["🔴 Ruptura", "🔴 Crítico", "🟠 Ponto de Pedido"])].sort_values(by="valor_total", ascending=False)
            if not df_risco.empty: st.warning(f"**{df_risco.iloc[0]['nome']}**: R$ {df_risco.iloc[0]['valor_total']:,.2f}")
            else: st.success("Nenhum item em risco.")
        with ac_c3:
            st.markdown("**📅 Cronograma de Suprimentos**")
            st.info(f"🚚 Próxima janela: **{data_entrega_str}**")

    st.divider()

    # ─── 2. TABELA ÚNICA CONSOLIDADA (FILTROS AVANÇADOS ABC/XYZ - OPÇÃO 4) ───
    st.subheader("📋 Posição Consolidada de Estoque & Suprimentos")
    if "filtro_status_pill" not in st.session_state: st.session_state["filtro_status_pill"] = "Todos"
    f_pills = st.columns(5)
    if f_pills[0].button(f"📊 Todos ({total_itens})", use_container_width=True, type="primary" if st.session_state["filtro_status_pill"] == "Todos" else "secondary"): st.session_state["filtro_status_pill"] = "Todos"; st.rerun()
    if f_pills[1].button(f"🔴 Ruptura ({n_ruptura})", use_container_width=True, type="primary" if st.session_state["filtro_status_pill"] == "🔴 Ruptura" else "secondary"): st.session_state["filtro_status_pill"] = "🔴 Ruptura"; st.rerun()
    if f_pills[2].button(f"🔴 Crítico ({n_critico})", use_container_width=True, type="primary" if st.session_state["filtro_status_pill"] == "🔴 Crítico" else "secondary"): st.session_state["filtro_status_pill"] = "🔴 Crítico"; st.rerun()
    if f_pills[3].button(f"🟠 Ponto Pedido ({n_ponto_ped})", use_container_width=True, type="primary" if st.session_state["filtro_status_pill"] == "🟠 Ponto de Pedido" else "secondary"): st.session_state["filtro_status_pill"] = "🟠 Ponto de Pedido"; st.rerun()
    if f_pills[4].button(f"🟢 OK ({n_ok})", use_container_width=True, type="primary" if st.session_state["filtro_status_pill"] == "🟢 OK" else "secondary"): st.session_state["filtro_status_pill"] = "🟢 OK"; st.rerun()

    cp1, cp2, cp3, cp4, cp5 = st.columns([1.2, 1.1, 1.1, 1.4, 1.2])
    with cp1: setor_sel = st.selectbox("⚡ Setor:", ["Todos"] + list(df["categoria"].unique()))
    with cp2: abc_sel = st.selectbox("🏆 Curva ABC:", ["Todas", "Classe A", "Classe B", "Classe C"])
    with cp3: xyz_sel = st.selectbox("🔍 Criticidade XYZ:", ["Todas", "Z (Vital)", "Y (Média)", "X (Baixa)"])
    with cp4: busca_nome = st.text_input("🔍 Busca por Insumo:")
    with cp5: apenas_compras_chk = st.checkbox("🛒 Apenas Compras", value=False)

    df_filtrado = df.copy()
    if st.session_state["filtro_status_pill"] != "Todos": 
        df_filtrado = df_filtrado[df_filtrado["Status"] == st.session_state["filtro_status_pill"]]
    if setor_sel != "Todos": 
        df_filtrado = df_filtrado[df_filtrado["categoria"] == setor_sel]
    if abc_sel != "Todas":
        df_filtrado = df_filtrado[df_filtrado["Classe_ABC"] == abc_sel]
    if xyz_sel != "Todas":
        target_c = "Z" if "Z" in xyz_sel else ("X" if "X" in xyz_sel else "Y")
        df_filtrado = df_filtrado[df_filtrado["criticidade"].fillna("Y").str.upper() == target_c]
    if busca_nome.strip(): 
        df_filtrado = df_filtrado[df_filtrado["nome"].str.contains(busca_nome, case=False)]
    if apenas_compras_chk: 
        df_filtrado = df_filtrado[df_filtrado["Sugestão Compra"] > 0]

    def destacar_status(val):
        if '🔴' in str(val): return 'background-color: rgba(239, 68, 68, 0.35); color: #000000; font-weight: bold;'
        if '🟠' in str(val): return 'background-color: rgba(245, 158, 11, 0.35); color: #000000; font-weight: bold;'
        if '🟢' in str(val): return 'background-color: rgba(16, 185, 129, 0.35); color: #000000; font-weight: bold;'
        return ''

    def format_tendencia(t):
        if pd.isna(t) or t is None:
            return "➡️ Estável"
        try:
            val = float(t)
            if val == 0: return "➡️ Estável"
            if val > 0: return f"📈 +{val:.0f}%"
            return f"📉 {val:.0f}%"
        except (ValueError, TypeError):
            return str(t) if str(t).strip() else "➡️ Estável"

    df_filtrado["criticidade"] = df_filtrado["criticidade"].fillna("Y").str.upper()
    df_filtrado["Valor_Total_Txt"] = df_filtrado["valor_total"].apply(lambda v: f"R$ {v:,.2f}")
    df_filtrado["Tendência"] = df_filtrado["tendencia"].apply(format_tendencia)
    
    display_df = df_filtrado[[
        'Status', 'categoria', 'nome', 'Classe_ABC', 'criticidade', 'saldo_atual', 'Ponto_Pedido', 
        'Runway_Txt', 'Tendência', 'Valor_Total_Txt', 'Sugestão Compra', 'Previsão de Entrega'
    ]].rename(
        columns={
            'categoria': 'Setor', 
            'nome': 'Produto / Insumo', 
            'Classe_ABC': 'ABC',
            'criticidade': 'Crit.', 
            'saldo_atual': 'Saldo Físico', 
            'Ponto_Pedido': 'Ponto Pedido', 
            'Runway_Txt': 'Cobertura (Runway)', 
            'Valor_Total_Txt': 'Valuation (R$)', 
            'Sugestão Compra': 'Sugestão Compra (un)', 
            'Previsão de Entrega': 'Previsão Entrega'
        }
    )
    st.dataframe(display_df.style.map(destacar_status, subset=['Status']), hide_index=True, width='stretch')

    # ─── 3. CONTAINER SANFONADO DE ANÁLISES GRÁFICAS COMPLETA ───
    st.divider()
    with st.expander("📊 Análises Gráficas, Curva ABC/XYZ & Previsão Preditiva", expanded=False):
        g_tabs = st.tabs(["📈 Distribuição & Giro", "🏆 Curva ABC (Financeiro)", "🔍 Matriz ABC-XYZ (Criticidade)", "🎯 Matriz de Risco & Lead Time", "🔮 Previsão Preditiva & Posse"])
        with g_tabs[0]:
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### 📊 Giro Total por Categoria")
                giro_setor = df.groupby("categoria")["total"].sum().reset_index().rename(columns={"categoria": "Setor", "total": "Movimentações"})
                fig_giro = px.bar(giro_setor, x="Setor", y="Movimentações", color="Movimentações", text_auto=True, color_continuous_scale="Viridis")
                apply_premium_chart_theme(fig_giro); st.plotly_chart(fig_giro, use_container_width=True)
            with g2:
                st.markdown("##### 🏆 Capital por Setor")
                valor_setor = df.groupby("categoria")["valor_total"].sum().reset_index().rename(columns={"categoria": "Setor", "valor_total": "Valor Total"})
                fig_pie = px.pie(valor_setor, values="Valor Total", names="Setor", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                apply_premium_chart_theme(fig_pie); st.plotly_chart(fig_pie, use_container_width=True)
        with g_tabs[1]:
            st.markdown("##### 🏆 Análise Pareto Curva ABC")
            df_abc_tab = df.sort_values(by="valor_total", ascending=False).copy()
            total_valor_tab = df_abc_tab["valor_total"].sum()
            if total_valor_tab > 0:
                df_abc_tab["valor_acumulado"] = df_abc_tab["valor_total"].cumsum()
                df_abc_tab["perc_acumulado"] = (df_abc_tab["valor_acumulado"] / total_valor_tab) * 100
                fig_abc = go.Figure()
                fig_abc.add_trace(go.Bar(x=df_abc_tab["nome"], y=df_abc_tab["valor_total"], name="Valor"))
                fig_abc.add_trace(go.Scatter(x=df_abc_tab["nome"], y=df_abc_tab["perc_acumulado"], name="% Acumulado", yaxis="y2"))
                apply_premium_chart_theme(fig_abc, is_dual_axis=True); st.plotly_chart(fig_abc, use_container_width=True)
        with g_tabs[2]:
            st.markdown("##### 🔍 Matriz Cruzada ABC-XYZ")
            df_matriz = df.copy()
            df_matriz["XYZ"] = df_matriz["criticidade"].apply(lambda c: "Z (Vital)" if str(c).upper()=="Z" else ("X (Baixa)" if str(c).upper()=="X" else "Y (Média)"))
            tabela_cruzada = pd.crosstab(df_matriz["Classe_ABC"], df_matriz["XYZ"], values=df_matriz["valor_total"], aggfunc="sum").fillna(0)
            fig_heatmap = px.imshow(tabela_cruzada, color_continuous_scale="Blues", text_auto=".2f")
            apply_premium_chart_theme(fig_heatmap); st.plotly_chart(fig_heatmap, use_container_width=True)
        with g_tabs[3]:
            st.markdown("##### 🎯 Matriz Dinâmica de Risco")
            df_scatter = df.copy()
            df_scatter['Runway_Scatter'] = df_scatter['Runway'].apply(lambda x: 45 if x == 999 else min(x, 45))
            fig_scatter = px.scatter(df_scatter, x="Runway_Scatter", y="lead_time", color="Status", size=df_scatter["saldo_atual"].clip(lower=8))
            apply_premium_chart_theme(fig_scatter); st.plotly_chart(fig_scatter, use_container_width=True)
        with g_tabs[4]:
            st.markdown("##### 🔮 Previsão Preditiva")
            from utils.consumption import calcular_previsao_demanda_preditiva, calcular_custo_posse_estoque
            df_pred_tab = calcular_previsao_demanda_preditiva(df, metodo=metodo_consumo, janela_dias=janela_dias)
            info_posse_tab = calcular_custo_posse_estoque(df_pred_tab)
            c_p1, c_p2, c_p3, c_p4 = st.columns(4)
            c_p1.metric("💰 Valuation", f"R$ {info_posse_tab.get('valuation_total', 0):,.2f}")
            c_p2.metric("📦 Custo Posse", f"R$ {info_posse_tab.get('custo_posse_mensal', 0):,.2f}")
            c_p3.metric("🛑 Capital Parado", f"R$ {info_posse_tab.get('valor_capital_parado', 0):,.2f}")
            c_p4.metric("🔮 Prev. 30d", f"{df_pred_tab['prev_30d'].sum():,.0f} un.")
            st.dataframe(df_pred_tab.head(), hide_index=True, use_container_width=True)

    # ─── EXPORTAÇÃO DO RELATÓRIO EXECUTIVO COMPLETO ───
    st.divider()
    from utils.reports import gerar_html_pdf_estoque
    from database.queries import listar_movimentacoes
    html_report_data = gerar_html_pdf_estoque(df, listar_movimentacoes(), None, metodo=metodo_consumo, janela_dias=janela_dias)
    col_rep1, col_rep2 = st.columns([3, 1])
    with col_rep1:
        st.markdown("##### 📄 Exportação do Relatório Executivo Completo WMS 5.0")
        st.caption("Gere um relatório consolidado com Valuation, Curva ABC/XYZ, Análise Preditiva e Custo de Posse em formato HTML/PDF pronto para impressão.")
    with col_rep2:
        st.download_button(label="📄 Baixar Relatório", data=html_report_data, file_name=f"Relatorio_Executivo_WMS_{datetime.now().strftime('%Y%m%d_%H%M')}.html", mime="text/html", use_container_width=True, type="primary")
