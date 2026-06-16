import datetime
import pandas as pd
import numpy as np
import streamlit as st
from utils.date_helpers import obter_cronograma_mes, calcular_previsao_entrega

def render_schedule_ui(df):
    st.subheader("📅 Cronograma Integrado de Compras & Fluxo Logístico")
    st.caption("Acompanhamento das etapas do ciclo de abastecimento: da abertura da solicitação até a entrega física na prateleira.")
    
    if df.empty:
        st.info("Cadastre insumos na aba de Configurações para habilitar o cronograma de compras.")
        return

    # --- 1. SELEÇÃO DE MÊS DE REFERÊNCIA ---
    hoje = datetime.date.today()
    
    # Determinar meses disponíveis para consulta (mês atual, próximo e subsequente)
    opcoes_meses = []
    mes_atual = hoje.month
    ano_atual = hoje.year
    
    for i in range(-1, 3):  # Mês anterior, Atual, Próximo, Subsequente
        m = mes_atual + i
        a = ano_atual
        if m < 1:
            m += 12
            a -= 1
        elif m > 12:
            m -= 12
            a += 1
        opcoes_meses.append((a, m))
        
    def formatar_opcao(opt):
        nomes_meses = [
            "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ]
        a, m = opt
        prefixo = ""
        if (a, m) == (ano_atual, mes_atual):
            prefixo = " (Mês Atual)"
        elif (a, m) == (ano_atual, mes_atual + 1) or (m == 1 and mes_atual == 12 and a == ano_atual + 1):
            prefixo = " (Próximo Ciclo)"
        return f"{nomes_meses[m]} de {a}{prefixo}"

    c_sel1, c_sel2 = st.columns([2, 1])
    with c_sel1:
        mes_sel = st.selectbox(
            "📅 Selecione o Mês de Referência do Cronograma:",
            opcoes_meses,
            index=2,  # Pré-seleciona o Próximo Ciclo (geralmente onde estão as ações de planejamento)
            format_func=formatar_opcao
        )
    
    # Obter cronograma do mês selecionado
    ano_c, mes_c = mes_sel
    crono = obter_cronograma_mes(ano_c, mes_c)
    
    # --- 2. CÁLCULO DOS STATUS DAS ETAPAS ---
    status_sol = ""
    status_proc = ""
    status_ent = ""
    
    # Cores/Estilos de badges
    style_active = "background-color: rgba(59, 130, 246, 0.15); color: #3b82f6; border: 1px solid #3b82f6; font-weight: 600; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; display: inline-block; margin-top: 5px;"
    style_completed = "background-color: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid #10b981; font-weight: 600; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; display: inline-block; margin-top: 5px;"
    style_pending = "background-color: rgba(148, 163, 184, 0.15); color: #64748b; border: 1px solid #94a3b8; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; display: inline-block; margin-top: 5px;"
    style_warning = "background-color: rgba(245, 158, 11, 0.15); color: #d97706; border: 1px solid #f59e0b; font-weight: 600; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; display: inline-block; margin-top: 5px;"
    
    st_sol_style = style_pending
    st_proc_style = style_pending
    st_ent_style = style_pending
    
    # Fase 1: Solicitação
    if hoje < crono["inicio_solicitacao"]:
        status_sol = "Aguardando Abertura"
        st_sol_style = style_pending
    elif crono["inicio_solicitacao"] <= hoje <= crono["fim_solicitacao"]:
        status_sol = "ABERTA (Janela Ativa)"
        st_sol_style = style_warning
    else:
        status_sol = "Janela Finalizada"
        st_sol_style = style_completed
        
    # Fase 2: Processamento Interno
    if hoje < crono["inicio_analise"]:
        status_proc = "Aguardando Prazo"
        st_proc_style = style_pending
    elif crono["inicio_analise"] <= hoje <= crono["data_aprovacao"]:
        status_proc = "Em Análise de Compras"
        st_proc_style = style_active
    else:
        status_proc = "Pedidos Emitidos"
        st_proc_style = style_completed
        
    # Fase 3: Entrega Fornecedor
    if hoje < crono["data_aprovacao"]:
        status_ent = "Aguardando Emissão"
        st_ent_style = style_pending
    elif crono["data_aprovacao"] <= hoje <= crono["data_entrega"]:
        status_ent = "Itens em Trânsito"
        st_ent_style = style_active
    else:
        status_ent = "Entregue e Disponível"
        st_ent_style = style_completed

    # --- 3. COUNTDOWN TIMER E EXIBIÇÃO DE INFORMAÇÕES GERAIS ---
    with c_sel2:
        st.write("")
        st.write("")
        # Se a janela de solicitação do próximo ciclo está futura
        next_crono = calcular_previsao_entrega()
        if hoje < next_crono["inicio_solicitacao"]:
            dias_restantes = (next_crono["inicio_solicitacao"] - hoje).days
            st.metric("⏳ Solicitação de Compras", f"Em {dias_restantes} dias", help="Dias restantes para o início da próxima janela de compras no sistema.")
        elif next_crono["inicio_solicitacao"] <= hoje <= next_crono["fim_solicitacao"]:
            st.markdown("<div style='text-align:center;'><span style='background-color:#ef4444; color:white; padding: 10px 15px; border-radius:8px; font-weight:bold; font-size:1.1rem; display:block;'>📝 JANELA DE COMPRAS ABERTA!</span></div>", unsafe_allow_html=True)
        else:
            st.write("Fora da janela de solicitação")

    # --- 4. STEPPER VISUAL DO CRONOGRAMA ---
    inicio_sol_f = crono["inicio_solicitacao"].strftime("%d/%m")
    fim_sol_f = crono["fim_solicitacao"].strftime("%d/%m")
    inicio_an_f = crono["inicio_analise"].strftime("%d/%m")
    aprov_f = crono["data_aprovacao"].strftime("%d/%m")
    entrega_f = crono["data_entrega"].strftime("%d/%m")
    
    st.markdown(f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: var(--secondary-background-color); padding: 25px 20px; border-radius: 12px; border: 1px solid rgba(128,128,128,0.15); margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.02);">
        <div style="flex: 1; text-align: center;">
            <div style="font-size: 1.8rem; margin-bottom: 5px;">📝</div>
            <strong style="color: var(--text-color); font-size: 0.95rem;">1. Solicitação de Compras</strong><br>
            <span style="font-size: 0.85rem; color: #888888;">Período: <b>{inicio_sol_f}</b> a <b>{fim_sol_f}</b></span><br>
            <span style="{st_sol_style}">{status_sol}</span>
        </div>
        <div style="font-size: 1.5rem; color: #3b82f6; font-weight: bold; padding: 0 10px;">➔</div>
        <div style="flex: 1; text-align: center;">
            <div style="font-size: 1.8rem; margin-bottom: 5px;">💼</div>
            <strong style="color: var(--text-color); font-size: 0.95rem;">2. Processamento Interno</strong><br>
            <span style="font-size: 0.85rem; color: #888888;">Período: <b>{inicio_an_f}</b> a <b>{aprov_f}</b></span><br>
            <span style="{st_proc_style}">{status_proc}</span>
        </div>
        <div style="font-size: 1.5rem; color: #3b82f6; font-weight: bold; padding: 0 10px;">➔</div>
        <div style="flex: 1; text-align: center;">
            <div style="font-size: 1.8rem; margin-bottom: 5px;">🚚</div>
            <strong style="color: var(--text-color); font-size: 0.95rem;">3. Entrega do Fornecedor</strong><br>
            <span style="font-size: 0.85rem; color: #888888;">Prazo Limite: <b>{entrega_f}</b></span><br>
            <span style="{st_ent_style}">{status_ent}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 5. DETALHES DAS REGRAS LOGÍSTICAS ---
    with st.expander("ℹ️ Regras Operacionais de Abastecimento (Informações)", expanded=False):
        st.markdown(f"""
        * **Prazo de Solicitação**: As requisições de reabastecimento devem ser salvas de **3 a 5 dias antes do fechamento do mês anterior** (para o mês de {formatar_opcao(mes_sel).split(' de ')[0]}, a janela vai de `{inicio_sol_f}` a `{fim_sol_f}`).
        * **Início de Análise (Setor de Compras)**: O setor administrativo/compras consolida e inicia a análise no **primeiro dia útil do mês de referência** (`{inicio_an_f}`).
        * **Lead Time Interno (5 dias úteis)**: Prazo do setor de compras para realizar cotações e aprovar os pedidos (`{inicio_an_f}` até `{aprov_f}`).
        * **Lead Time Externo / Fornecedor (3 dias úteis)**: Prazo máximo estipulado para que o fornecedor efetue a entrega física no almoxarifado (`{aprov_f}` até `{entrega_f}`).
        * **Lead Time Total**: **8 dias úteis** contados a partir do início do mês de referência.
        """)

    # --- 6. LISTA DE COMPRAS SUGERIDAS PARA ESTE CICLO ---
    st.markdown("### 🛒 Planejamento e Itens do Ciclo de Compras")
    
    # Calcular sugestão de compra dos itens com base no banco atual
    # Para isso, primeiro rodamos os mesmos cálculos do Dashboard
    df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]
    total_valor = df["valor_total"].sum()
    
    # Classificação ABC rápida
    df_abc = df.sort_values(by="valor_total", ascending=False).copy()
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
        classes_map = {id_p: "Classe C" for id_p in df["id"]}
        
    df["Classe_ABC"] = df["id"].map(classes_map).fillna("Classe C")
    
    # Fatores padrão (1.4 para A, 1.2 para B, 1.1 para C)
    def obter_fator(row):
        classe = row["Classe_ABC"]
        if class_map_val := {"Classe A": 1.4, "Classe B": 1.2, "Classe C": 1.1}.get(classe):
            return class_map_val
        return 1.1
        
    df["Fator_Seguranca"] = df.apply(obter_fator, axis=1)
    
    # Simulação de consumo médio nos últimos 30 dias (para calcular consumo diário)
    from database.connection import get_conn
    with get_conn() as conn:
        movs = pd.read_sql("""
            SELECT id_produto, SUM(ABS(quantidade)) as total
            FROM movimentacoes 
            WHERE (tipo='Saída' OR (tipo='Contagem' AND quantidade < 0))
              AND data_hora >= date('now', '-30 days')
            GROUP BY id_produto
        """, conn)
    cons_dict = dict(zip(movs['id_produto'], movs['total'])) if not movs.empty else {}
    
    df['total_30d'] = df['id'].map(cons_dict).fillna(0)
    df['consumo_diario'] = df['total_30d'] / 30.0
    
    # Mínimo Ideal e Sugestão de Compra
    minimo_calculado = np.ceil(df["consumo_diario"] * df["lead_time"] * df["Fator_Seguranca"]).astype(int)
    df["Minimo Ideal"] = np.maximum(df["estoque_minimo"], minimo_calculado)
    df["Sugestão Compra"] = (df["Minimo Ideal"] - df["saldo_atual"]).clip(lower=0)
    df["Custo Estimado (R$)"] = df["Sugestão Compra"] * df["valor_unitario"]
    
    # Filtrar apenas produtos que necessitam de compras para este ciclo
    df_compras_ciclo = df[df["Sugestão Compra"] > 0].copy()
    
    if not df_compras_ciclo.empty:
        # Valuation do pedido do ciclo
        total_custo_ciclo = df_compras_ciclo["Custo Estimado (R$)"].sum()
        total_itens_comprar = df_compras_ciclo["Sugestão Compra"].sum()
        
        c_kpi1, c_kpi2, c_kpi3 = st.columns(3)
        c_kpi1.metric("📦 Total de Insumos do Pedido", f"{len(df_compras_ciclo)} itens")
        c_kpi2.metric("🔢 Quantidade Total de Unidades", f"{int(total_itens_comprar)} un")
        c_kpi3.metric("💰 Custo Estimado do Pedido", f"R$ {total_custo_ciclo:,.2f}")
        
        st.write("---")
        st.markdown("**📋 Insumos a serem solicitados nesta janela:**")
        
        df_display = df_compras_ciclo[["categoria", "nome", "saldo_atual", "Minimo Ideal", "Sugestão Compra", "valor_unitario", "Custo Estimado (R$)"]].rename(
            columns={
                "categoria": "Setor",
                "nome": "Produto",
                "saldo_atual": "Saldo Físico",
                "Minimo Ideal": "Mínimo Ideal",
                "Sugestão Compra": "Qtd. a Comprar",
                "valor_unitario": "Preço Unitário",
                "Custo Estimado (R$)": "Total Estimado"
            }
        )
        
        st.dataframe(
            df_display.style.format({
                "Preço Unitário": "R$ {:.2f}",
                "Total Estimado": "R$ {:.2f}"
            }),
            hide_index=True,
            use_container_width=True
        )
    else:
        st.success("🎉 **Excelente!** Com base nos saldos atuais de prateleira e ritmos de consumo, não há nenhum insumo necessitando de compras para este ciclo.")
