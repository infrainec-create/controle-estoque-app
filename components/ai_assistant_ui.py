import pandas as pd
import streamlit as st
import google.generativeai as genai
from database.connection import get_conn

@st.cache_data(ttl=3600)
def obter_modelos_gemini(api_key):
    try:
        genai.configure(api_key=api_key)
        modelos = [m.name.replace('models/', '') for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        return modelos if modelos else ["gemini-1.5-flash", "gemini-1.5-pro"]
    except Exception:
        return ["gemini-1.5-flash", "gemini-1.5-pro"]

def render_ai_assistant_ui(df):
    st.subheader("🧠 Analista IA & Previsão de Demanda WMS 5.0")
    if df.empty:
        st.info("Cadastre insumos para ativar o painel preditivo de demanda e a análise de Inteligência Artificial.")
        return

    try:
        # Validação segura das credenciais do Gemini
        if "GEMINI_API_KEY" not in st.secrets or st.secrets["GEMINI_API_KEY"] == "sua_chave_gemini_aqui":
            st.warning("⚠️ O Assistente de IA está inativo. A chave `GEMINI_API_KEY` não está configurada ou possui o valor padrão no arquivo `.streamlit/secrets.toml`. Insira sua chave de API válida para habilitar a inteligência preditiva.")
            return
            
        # Seleção simplificada e robusta de versão de modelo
        modelos_validos = obter_modelos_gemini(st.secrets["GEMINI_API_KEY"])
        
        c1, c2 = st.columns([2, 1])
        with c1:
            modelo_selecionado = st.selectbox("🤖 Versão do Modelo Gemini:", modelos_validos, index=0)
        with c2:
            st.write("") # alinhamento
            st.write("")
            limpar_chat = st.button("🗑️ Limpar Histórico de Chat", use_container_width=True)

        if limpar_chat:
            st.session_state["gemini_chat_history"] = []
            st.toast("Histórico do chat limpo!", icon="🧹")
            st.rerun()

        # ─────────────────────────────────────────────────────────────
        # CÁLCULO DAS MÉTRICAS DE PREVISÃO OPERACIONAL E GATILHOS
        # ─────────────────────────────────────────────────────────────
        import datetime
        hoje = datetime.date.today()
        pattern_atual = f"%/{hoje.month:02d}/{hoje.year}%"
        
        with get_conn() as conn:
            cons = conn.execute("""
                SELECT id_produto, SUM(ABS(quantidade)) 
                FROM movimentacoes 
                WHERE (tipo='Saída' OR (tipo='Contagem' AND quantidade < 0))
                  AND data_hora LIKE ?
                GROUP BY id_produto
            """, (pattern_atual,)).fetchall()
            
            # Se não houver consumo no mês atual (ex: início do mês), usa histórico de saídas como fallback
            if not cons or sum(r[1] for r in cons) == 0:
                cons = conn.execute("""
                    SELECT id_produto, SUM(ABS(quantidade)) 
                    FROM movimentacoes 
                    WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0)
                    GROUP BY id_produto
                """).fetchall()
                
            recente_movs = pd.read_sql("""
                SELECT m.data_hora, p.nome AS produto, m.tipo, m.quantidade, m.saldo_resultante, m.observacao 
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto 
                ORDER BY m.id DESC LIMIT 10
            """, conn)
            
        cons_dict = dict(cons)
        df_prev = df.copy()
        
        # Consumo mensal (últimos 30 dias) e velocidade diária
        df_prev['consumo_mensal'] = df_prev['id'].map(cons_dict).fillna(0).astype(int)
        df_prev['consumo_diario'] = df_prev['consumo_mensal'] / 30.0
        
        # Runway (Cobertura de estoque em dias)
        df_prev['Runway'] = 999
        mask = df_prev['consumo_diario'] > 0
        df_prev.loc[mask, 'Runway'] = (df_prev.loc[mask, 'saldo_atual'] / df_prev.loc[mask, 'consumo_diario']).astype(int)
        
        # Definição matemática de gatilho de compra
        def set_gatilho(row):
            saldo = row['saldo_atual']
            runway = row['Runway']
            lead = row['lead_time']
            minimo = row['estoque_minimo']
            
            if saldo <= 0:
                return "🚨 RUPTURA (Saldo Zero)"
            if saldo < minimo or (runway != 999 and runway <= lead):
                return "⚠️ COMPRA URGENTE"
            if runway != 999 and runway <= (lead + 3):
                return "🟠 COMPRA PREVENTIVA"
            return "🟢 ADEQUADO"
            
        df_prev['Gatilho'] = df_prev.apply(set_gatilho, axis=1)
        
        # Sugestão de quantidade a comprar para cobrir 30 dias
        def calc_sugestao(row):
            gatilho = row['Gatilho']
            if "🟢 ADEQUADO" in gatilho:
                return 0
            cd = row['consumo_diario']
            saldo = row['saldo_atual']
            minimo = row['estoque_minimo']
            alvo = int((30 * cd) + minimo)
            sugerido = max(0, alvo - saldo)
            return int(sugerido)
            
        df_prev['Sugerido'] = df_prev.apply(calc_sugestao, axis=1)

        # ─────────────────────────────────────────────────────────────
        # RENDERIZAÇÃO DO EXPANDER DE PREVISÃO DE DEMANDA
        # ─────────────────────────────────────────────────────────────
        with st.expander("🔮 **Painel de Previsão de Demanda & Gatilhos de Compras WMS 5.0**", expanded=True):
            st.caption("Cálculo preditivo em tempo real com base no ritmo médio de consumo (últimos 30 dias) e lead time de fornecedores.")
            
            df_display = df_prev.copy()
            df_display['Runway_Txt'] = df_display['Runway'].apply(lambda x: "Sem consumo recente" if x == 999 else f"{x} dias")
            df_display['consumo_diario_txt'] = df_display['consumo_diario'].apply(lambda x: f"{x:.2f} un/dia")
            df_display['sugerido_txt'] = df_display['Sugerido'].apply(lambda x: "Estoque OK" if x <= 0 else f"Comprar {x} un")
            
            df_display = df_display.rename(columns={
                "nome": "Insumo",
                "categoria": "Setor",
                "saldo_atual": "Saldo Atual",
                "lead_time": "Lead Time (Forn.)",
                "consumo_diario_txt": "Velocidade Consumo",
                "Runway_Txt": "Cobertura (Runway)",
                "Gatilho": "Status de Gatilho",
                "sugerido_txt": "Sugestão de Reposição"
            })
            
            # Formatação visual colorida da tabela preditiva
            def style_gatilho(row):
                gatilho = row['Status de Gatilho']
                color = ''
                if "🚨" in gatilho:
                    color = 'background-color: rgba(239, 68, 68, 0.08); color: #ef4444; font-weight: bold;'
                elif "⚠️" in gatilho:
                    color = 'background-color: rgba(245, 158, 11, 0.08); color: #f59e0b; font-weight: bold;'
                elif "🟠" in gatilho:
                    color = 'background-color: rgba(59, 130, 246, 0.08); color: #3b82f6;'
                else:
                    color = 'background-color: rgba(16, 185, 129, 0.08); color: #10b981;'
                return [color if col == 'Status de Gatilho' else '' for col in row.index]
                
            df_styled = df_display[["Setor", "Insumo", "Saldo Atual", "Velocidade Consumo", "Lead Time (Forn.)", "Cobertura (Runway)", "Status de Gatilho", "Sugestão de Reposição"]].style.apply(style_gatilho, axis=1)
            st.dataframe(df_styled, use_container_width=True, hide_index=True)
            
            st.markdown("""
            💡 **Entendendo os Gatilhos Preditivos:**
            * `🚨 RUPTURA`: Insumo totalmente esgotado no estoque real. Reposição imediata obrigatória.
            * `⚠️ COMPRA URGENTE`: O saldo atual caiu abaixo do estoque mínimo de segurança **OU** o tempo que você tem de estoque (Runway) é menor que o tempo de entrega do fornecedor (Lead Time).
            * `🟠 COMPRA PREVENTIVA`: O item está com cobertura extra muito próxima ao tempo de entrega do fornecedor (margem de segurança de 3 dias).
            """)

        # ─────────────────────────────────────────────────────────────
        # HISTÓRICO E CHAT COM O ASSISTENTE IA
        # ─────────────────────────────────────────────────────────────
        st.write("---")
        st.markdown("### 💬 Assistente de Suporte à Decisão & Chat de Auditoria")
        
        # Inicializa o histórico de chat se não existir
        if "gemini_chat_history" not in st.session_state:
            st.session_state["gemini_chat_history"] = []

        # Container de Chat com Rolagem Vertical Fixa (Melhor UX)
        chat_container = st.container(height=450)
        with chat_container:
            for msg in st.session_state["gemini_chat_history"]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        # Contexto operacional injetado de forma estruturada para o Gemini
        posicao_estoque_md = df_prev[['Gatilho', 'categoria', 'nome', 'saldo_atual', 'estoque_minimo', 'lead_time', 'Runway', 'Sugerido']].rename(columns={
            'nome': 'Insumo', 'categoria': 'Setor', 'saldo_atual': 'Saldo', 'estoque_minimo': 'Minimo', 'lead_time': 'LeadTime_Dias', 'Sugerido': 'SugestaoComprar'
        }).to_markdown(index=False)
        
        movimentacoes_recente_md = recente_movs.to_markdown(index=False) if not recente_movs.empty else "Nenhuma movimentação registrada no histórico."

        system_context = f"""
        Você é o Analista Logístico Preditivo Sênior do WMS 5.0, responsável pela inteligência de suprimentos de um Almoxarifado de Insumos.
        Seu objetivo principal é guiar o operador na tomada de decisões estratégicas de compras, otimização de estoque, mitigação de riscos de ruptura e auditoria.
        
        POSIÇÃO DE ESTOQUE ATUAL & MÉTRICAS PREDITIVAS CALCULADAS (Runway = Dias de Cobertura, SugestaoComprar = Sugestão de Reposição):
        {posicao_estoque_md}
        
        ÚLTIMAS 10 MOVIMENTAÇÕES DE REGISTRO HISTÓRICO:
        {movimentacoes_recente_md}
        
        Instruções de resposta:
        1. Responda em português brasileiro de forma altamente técnica, clara e extremamente concisa.
        2. Utilize tabelas markdown e listas com marcadores para estruturar diagnósticos e recomendações.
        3. Você está dialogando diretamente com o Gestor/Administrador do WMS. Seja analítico e profissional.
        """

        # --- AÇÕES RÁPIDAS (BOTOEIRA PREDITIVA) ---
        c_act1, c_act2 = st.columns(2)
        with c_act1:
            gerar_diagnostico = st.button("✨ Girar Diagnóstico Geral do Almoxarifado", type="secondary", use_container_width=True)
        with c_act2:
            gerar_plano_compras = st.button("🔮 Gerar Plano de Compras Preditivo (IA)", type="primary", use_container_width=True)
            
        # Botão de exportação do último diagnóstico gerado pela IA
        if st.session_state.get("gemini_chat_history"):
            respostas_ia = [m for m in st.session_state["gemini_chat_history"] if m["role"] == "assistant"]
            if respostas_ia:
                from datetime import datetime
                ultima_resposta = respostas_ia[-1]["content"]
                st.download_button(
                    label="📥 Exportar Último Diagnóstico da IA (.md)",
                    data=ultima_resposta,
                    file_name=f"diagnostico_ia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                    mime="text/markdown",
                    use_container_width=True
                )
            
        if gerar_diagnostico:
            st.session_state["gemini_chat_history"].append({"role": "user", "content": "Gere um Diagnóstico Geral do Almoxarifado."})
            with chat_container:
                with st.chat_message("user"):
                    st.write("Gere um Diagnóstico Geral do Almoxarifado.")
                    
                with st.chat_message("assistant"):
                    with st.spinner("Analisando dados logísticos..."):
                        prompt = f"""
                        {system_context}
                        
                        Por favor, elabore um Diagnóstico Logístico estratégico contendo:
                        1. **Análise de Saúde do Estoque**: Uma visão do estado geral do inventário do WMS 5.0.
                        2. **Riscos Imediatos**: Alertas sobre itens críticos ou em ruptura física de saldo.
                        3. **Sugestões Operacionais**: Dicas para melhorar o giro físico de materiais.
                        """
                        mod = genai.GenerativeModel(modelo_selecionado)
                        resposta = mod.generate_content(prompt).text
                        st.markdown(resposta)
                        st.session_state["gemini_chat_history"].append({"role": "assistant", "content": resposta})
            st.rerun()

        if gerar_plano_compras:
            st.session_state["gemini_chat_history"].append({"role": "user", "content": "Gere o Plano de Ação de Compras Preditivo."})
            with chat_container:
                with st.chat_message("user"):
                    st.write("Gere o Plano de Ação de Compras Preditivo.")
                    
                with st.chat_message("assistant"):
                    with st.spinner("Compilando previsões e lead times..."):
                        prompt = f"""
                        {system_context}
                        
                        Por favor, elabore um Plano Estratégico de Compras Preditivo contendo:
                        1. **Gargalos Operacionais**: Quais setores de suprimentos exigem compras imediatas baseadas nos lead times.
                        2. **Sugestão de Pedido de Compra**: Uma tabela markdown detalhada com: Insumo, Quantidade Recomendada (com base no 'SugestaoComprar'), Justificativa Preditiva (relação de Runway vs Lead Time) e Prioridade de Compra (Alta/Média/Baixa).
                        3. **Avisos de Abastecimento**: Alertas sobre itens com fornecedores lentos que necessitam de compras antecipadas permanentes.
                        """
                        mod = genai.GenerativeModel(modelo_selecionado)
                        resposta = mod.generate_content(prompt).text
                        st.markdown(resposta)
                        st.session_state["gemini_chat_history"].append({"role": "assistant", "content": resposta})
            st.rerun()

        # --- CHAT INPUT ---
        user_query = st.chat_input("Pergunte algo sobre previsões de demanda, compras ou auditoria...")

        if user_query:
            st.session_state["gemini_chat_history"].append({"role": "user", "content": user_query})
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(user_query)

                with st.chat_message("assistant"):
                    with st.spinner("O Analista IA está processando..."):
                        # Compila histórico da conversa para manter a coerência do chat
                        historico_conversa = ""
                        for msg in st.session_state["gemini_chat_history"][-6:-1]: # Pega as últimas 5 mensagens
                            role_label = "Gestor" if msg["role"] == "user" else "Analista WMS"
                            historico_conversa += f"{role_label}: {msg['content']}\n"

                        prompt = f"""
                        {system_context}
                        
                        HISTÓRICO DA CONVERSA RECENTE:
                        {historico_conversa}
                        
                        PERGUNTA DO OPERADOR:
                        {user_query}
                        """
                        mod = genai.GenerativeModel(modelo_selecionado)
                        resposta = mod.generate_content(prompt).text
                        st.markdown(resposta)
                        st.session_state["gemini_chat_history"].append({"role": "assistant", "content": resposta})
            st.rerun()

    except Exception as e:
        st.error(f"Erro de comunicação com a API do Google Gemini: {e}")
