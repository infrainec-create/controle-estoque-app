import datetime
import pandas as pd
import numpy as np
import streamlit as st
from utils.date_helpers import obter_cronograma_mes, calcular_previsao_entrega, obter_parametros_cronograma
from database.connection import get_conn
from database.queries import registrar_log_auditoria
from utils.drive_sync import disparar_sincronizacao

def render_schedule_ui(df):
    st.subheader("📅 Cronograma Integrado de Compras & Fluxo Logístico")
    st.caption("Acompanhamento das etapas do ciclo de abastecimento: da abertura da solicitação até a entrega física na prateleira.")
    
    params = obter_parametros_cronograma()
    
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
        * **Prazo de Solicitação**: As requisições de reabastecimento devem ser salvas de **{params['dias_antes_fim_sol']} a {params['dias_antes_inicio_sol']} dias antes do fechamento do mês anterior** (para o mês de {formatar_opcao(mes_sel).split(' de ')[0]}, a janela vai de `{inicio_sol_f}` a `{fim_sol_f}`).
        * **Início de Análise (Setor de Compras)**: O setor administrativo/compras consolida e inicia a análise no **primeiro dia útil do mês de referência** (`{inicio_an_f}`).
        * **Lead Time Interno ({params['dias_uteis_analise']} dias úteis)**: Prazo do setor de compras para realizar cotações e aprovar os pedidos (`{inicio_an_f}` até `{aprov_f}`).
        * **Lead Time Externo / Fornecedor ({params['dias_uteis_entrega']} dias úteis)**: Prazo máximo estipulado para que o fornecedor efetue a entrega física no almoxarifado (`{aprov_f}` até `{entrega_f}`).
        * **Lead Time Total**: **{params['dias_uteis_analise'] + params['dias_uteis_entrega']} dias úteis** contados a partir do início do mês de referência.
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
    
    # Carrega fatores de segurança por setor configurados no banco
    fatores_setor = {}
    with get_conn() as conn:
        rows_f = conn.execute("SELECT chave, valor FROM configuracoes WHERE chave LIKE 'fator_seguranca_%'").fetchall()
        for k, v in rows_f:
            setor_nome = k.replace("fator_seguranca_", "")
            fatores_setor[setor_nome] = float(v)
            
    padroes = {"Limpeza": 1.1, "Copa": 1.1, "EPI": 1.2, "Escritório": 1.1, "Geral": 1.1}
    
    def obter_fator_setor(row):
        cat = row["categoria"]
        return fatores_setor.get(cat, padroes.get(cat, 1.1))
        
    df["Fator_Seguranca"] = df.apply(obter_fator_setor, axis=1)
    
    # 1. Determinar o mês/ano de projeção (mês anterior ao ciclo de referência selecionado)
    if mes_c == 1:
        ano_anterior = ano_c - 1
        mes_anterior = 12
    else:
        ano_anterior = ano_c
        mes_anterior = mes_c - 1
        
    # Definir os limites temporais do mês anterior para o cálculo
    t_start = datetime.datetime(ano_anterior, mes_anterior, 1)
    t_end = datetime.datetime(ano_c, mes_c, 1)
    
    metodo = st.session_state.get("metodo_consumo", "movimentacoes")
    
    from utils.consumption import obter_movimentacoes_processadas, calcular_consumo_intervalo
    try:
        with get_conn() as conn:
            movs = obter_movimentacoes_processadas(conn)
    except Exception:
        movs = pd.DataFrame()
        
    cons_dict = {}
    if not movs.empty:
        for _, row in df.iterrows():
            p_id = row['id']
            prod_movs = movs[movs['id_produto'] == p_id]
            consumo = calcular_consumo_intervalo(prod_movs, t_start, t_end, metodo)
            
            # Se der 0 e não houver nenhuma movimentação anterior no intervalo, 
            # fazemos um fallback para o histórico total
            if consumo == 0:
                t_init = datetime.datetime(1970, 1, 1)
                t_now = datetime.datetime.now()
                consumo_fallback = calcular_consumo_intervalo(prod_movs, t_init, t_now, metodo)
                if consumo_fallback > 0:
                    consumo = consumo_fallback
            cons_dict[p_id] = consumo
            
    # 3. Projetar consumo para o mês do ciclo
    df['consumo_projetado'] = df['id'].map(cons_dict).fillna(0).astype(int)
    df['consumo_diario'] = df['consumo_projetado'] / 30.0
    
    # 4. Mínimo Ideal (Estoque de Segurança para cobrir o Lead Time)
    minimo_calculado = np.ceil(df["consumo_diario"] * df["lead_time"] * df["Fator_Seguranca"]).astype(int)
    df["Minimo Ideal"] = np.maximum(df["estoque_minimo"], minimo_calculado)
    
    # 5. Sugestão de Compra para o ciclo: Cobrir o consumo projetado do mês seguinte + Estoque de Segurança
    df["Sugestão Compra"] = (df["consumo_projetado"] + df["Minimo Ideal"] - df["saldo_atual"]).clip(lower=0)
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

    # --- 7. CONFIGURAÇÃO DE PARÂMETROS DO CRONOGRAMA ---
    st.divider()
    st.markdown("### ⚙️ Configurações e Parâmetros do Ciclo de Abastecimento")
    
    is_admin = st.session_state.get("perfil_atual") == "Administrador"
    
    if is_admin:
        with st.expander("🛠️ Ajustar Parâmetros de Prazos e Lead Times (Apenas Administradores)", expanded=False):
            st.caption("Ajuste os parâmetros abaixo para recalcular dinamicamente as datas do cronograma e o cálculo do estoque mínimo ideal.")
            
            tab_params, tab_override = st.tabs(["⚙️ Parâmetros Globais", "📅 Sobrescrever Janela do Ciclo Atual"])
            
            with tab_params:
                with st.form("form_parametros_crono"):
                    col_c1, col_c2 = st.columns(2)
                    with col_c1:
                        novo_inicio_sol = st.number_input(
                            "Dias antes do fim do mês para INICIAR a janela de solicitação:",
                            min_value=1, max_value=28, value=int(params["dias_antes_inicio_sol"]),
                            help="Define quantos dias antes do último dia do mês o sistema abre o período de requisições."
                        )
                        novo_fim_sol = st.number_input(
                            "Dias antes do fim do mês para ENCERRAR a janela de solicitação:",
                            min_value=1, max_value=28, value=int(params["dias_antes_fim_sol"]),
                            help="Define quantos dias antes do último dia do mês o sistema encerra o período de requisições."
                        )
                    with col_c2:
                        novo_analise = st.number_input(
                            "Prazo de Processamento/Análise Interna de Compras (Dias Úteis):",
                            min_value=1, max_value=60, value=int(params["dias_uteis_analise"]),
                            help="Lead time administrativo interno do setor de compras."
                        )
                        novo_entrega = st.number_input(
                            "Prazo de Entrega do Fornecedor (Dias Úteis):",
                            min_value=1, max_value=60, value=int(params["dias_uteis_entrega"]),
                            help="Lead time do fornecedor externo para a entrega física."
                        )
                        
                    if st.form_submit_button("💾 Salvar e Atualizar Parâmetros Globais", type="primary", use_container_width=True):
                        if novo_fim_sol >= novo_inicio_sol:
                            st.error("❌ O início da janela de solicitação deve ser antes do fim (ex: início em 5 dias e fim em 3 dias antes do fechamento do mês).")
                        else:
                            try:
                                with get_conn() as conn:
                                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'crono_dias_antes_inicio_sol'", (str(novo_inicio_sol),))
                                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'crono_dias_antes_fim_sol'", (str(novo_fim_sol),))
                                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'crono_dias_uteis_analise'", (str(novo_analise),))
                                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'crono_dias_uteis_entrega'", (str(novo_entrega),))
                                
                                detalhe_log = (f"Parâmetros de cronograma alterados: Janela de solicitação={novo_inicio_sol} a {novo_fim_sol} dias antes; "
                                               f"Lead time interno={novo_analise} dias úteis; Lead time fornecedor={novo_entrega} dias úteis.")
                                registrar_log_auditoria(st.session_state["usuario_atual"], "Alterar Parâmetros Cronograma", detalhe_log)
                                
                                # Dispara o sincronismo e limpa caches
                                disparar_sincronizacao()
                                
                                st.toast("Parâmetros salvos e sincronizados!", icon="✅")
                                st.success("Configurações do cronograma atualizadas com sucesso!")
                                st.rerun()
                            except Exception as e_cfg:
                                st.error(f"Erro ao salvar configurações no banco de dados: {e_cfg}")
                                
            with tab_override:
                # Determina o nome do mês formatado
                nomes_meses_loc = [
                    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
                ]
                st.markdown(f"##### Ajuste Fino para o Ciclo de **{nomes_meses_loc[mes_c]} de {ano_c}**")
                st.caption("Ao aplicar uma data específica abaixo, ela anulará as regras de dias antes do mês apenas para este período.")
                
                # Procura se já existe override no banco
                key_override = f"crono_override_sol_{ano_c}_{mes_c}"
                override_atual = None
                try:
                    with get_conn() as conn:
                        row_ov = conn.execute("SELECT valor FROM configuracoes WHERE chave = ?", (key_override,)).fetchone()
                        if row_ov:
                            override_atual = row_ov[0]
                except Exception:
                    pass
                    
                # Calcula as datas padrão do helper de data
                from utils.date_helpers import obter_ultimo_dia_mes
                if mes_c == 1:
                    u_dia = obter_ultimo_dia_mes(datetime.date(ano_c - 1, 12, 1))
                else:
                    u_dia = obter_ultimo_dia_mes(datetime.date(ano_c, mes_c - 1, 1))
                
                default_inicio = u_dia - datetime.timedelta(days=int(params["dias_antes_inicio_sol"]))
                default_fim = u_dia - datetime.timedelta(days=int(params["dias_antes_fim_sol"]))
                
                if override_atual:
                    try:
                        p_ov = override_atual.split(":")
                        default_inicio = datetime.date.fromisoformat(p_ov[0])
                        default_fim = datetime.date.fromisoformat(p_ov[1])
                        st.info(f"💡 **Status:** Este ciclo possui datas personalizadas ativas: `{default_inicio.strftime('%d/%m/%Y')}` a `{default_fim.strftime('%d/%m/%Y')}`.")
                    except Exception:
                        pass
                else:
                    st.caption("🟢 **Status:** Usando a janela de solicitação calculada padrão (sem data fixa).")
                    
                with st.form("form_override_crono"):
                    c_o1, c_o2 = st.columns(2)
                    with c_o1:
                        data_ini_input = st.date_input("Data de INÍCIO da janela de solicitação:", value=default_inicio)
                    with c_o2:
                        data_fim_input = st.date_input("Data de FIM da janela de solicitação:", value=default_fim)
                        
                    c_ob1, c_ob2 = st.columns(2)
                    with c_ob1:
                        salvar_ov = st.form_submit_button("💾 Salvar Ajuste deste Ciclo", type="primary", use_container_width=True)
                    with c_ob2:
                        limpar_ov = st.form_submit_button("🔄 Restaurar Padrão do Ciclo", type="secondary", use_container_width=True)
                        
                if salvar_ov:
                    if data_ini_input >= data_fim_input:
                        st.error("❌ A data de início deve ser anterior à data de fim da janela de solicitação.")
                    else:
                        try:
                            valor_ov = f"{data_ini_input.isoformat()}:{data_fim_input.isoformat()}"
                            with get_conn() as conn:
                                conn.execute("INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES (?, ?)", (key_override, valor_ov))
                            
                            registrar_log_auditoria(st.session_state["usuario_atual"], "Ajustar Data Ciclo", 
                                                    f"Sobrescreveu data de solicitação para o ciclo {mes_c}/{ano_c}: {data_ini_input} a {data_fim_input}.")
                            disparar_sincronizacao()
                            st.toast("Datas salvas para este ciclo!", icon="✅")
                            st.success(f"Janela de solicitação ajustada para {data_ini_input.strftime('%d/%m/%Y')} a {data_fim_input.strftime('%d/%m/%Y')}!")
                            st.rerun()
                        except Exception as e_ov:
                            st.error(f"Erro ao salvar ajuste de data no banco: {e_ov}")
                            
                if limpar_ov:
                    try:
                        with get_conn() as conn:
                            conn.execute("DELETE FROM configuracoes WHERE chave = ?", (key_override,))
                        
                        registrar_log_auditoria(st.session_state["usuario_atual"], "Restaurar Data Ciclo", 
                                                f"Restaurou período padrão de solicitação para o ciclo {mes_c}/{ano_c}.")
                        disparar_sincronizacao()
                        st.toast("Datas restauradas para o padrão do ciclo!", icon="🔄")
                        st.success("O ciclo voltou a usar a regra de dias calculados antes do final do mês!")
                        st.rerun()
                    except Exception as e_ov:
                        st.error(f"Erro ao restaurar datas padrão: {e_ov}")
    else:
        st.info("🔒 **Painel de Configuração Reservado:** Apenas Administradores do sistema podem editar os prazos e parâmetros logísticos do cronograma de compras.")
