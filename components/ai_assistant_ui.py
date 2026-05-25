import pandas as pd
import streamlit as st
import google.generativeai as genai
from database.connection import get_conn

def render_ai_assistant_ui(df):
    st.subheader("🧠 Analista IA de Suprimentos & Auditoria")
    if df.empty:
        st.info("Cadastre insumos para ativar a análise de Inteligência Artificial.")
        return

    try:
        # Validação segura das credenciais do Gemini
        if "GEMINI_API_KEY" not in st.secrets or st.secrets["GEMINI_API_KEY"] == "sua_chave_gemini_aqui":
            st.warning("⚠️ O Assistente de IA está inativo. A chave `GEMINI_API_KEY` não está configurada ou possui o valor padrão no arquivo `.streamlit/secrets.toml`. Insira sua chave de API válida para habilitar o chat inteligente.")
            return
            
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        
        # Seleção simplificada e robusta de versão de modelo
        modelos_validos = [m.name.replace('models/', '') for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
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

        # Inicializa o histórico de chat se não existir
        if "gemini_chat_history" not in st.session_state:
            st.session_state["gemini_chat_history"] = []

        # Renderiza mensagens anteriores do histórico
        for msg in st.session_state["gemini_chat_history"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # --- GERAÇÃO DE CONTEXTO EM TEMPO REAL ---
        with get_conn() as conn:
            cons = conn.execute("SELECT id_produto, SUM(ABS(quantidade)) FROM movimentacoes WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0) GROUP BY id_produto").fetchall()
            recente_movs = pd.read_sql("""
                SELECT m.data_hora, p.nome AS produto, m.tipo, m.quantidade, m.observacao 
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto 
                ORDER BY m.id DESC LIMIT 10
            """, conn)
            
        cons_dict = dict(cons)
        
        # Prepara estatísticas detalhadas de Runway
        df_context = df.copy()
        df_context['consumo_mensal'] = df_context['id'].map(cons_dict).fillna(0).astype(int)
        df_context['consumo_diario'] = df_context['consumo_mensal'] / 30
        mask = df_context['consumo_diario'] > 0
        df_context['Runway'] = 999
        df_context.loc[mask, 'Runway'] = (df_context.loc[mask, 'saldo_atual'] / df_context.loc[mask, 'consumo_diario']).astype(int)
        df_context['Cobertura'] = df_context['Runway'].apply(lambda x: "Sem consumo" if x == 999 else f"{x} dias")
        
        def set_status(row):
            if row['saldo_atual'] <= 0: return '🔴 Ruptura'
            if row['saldo_atual'] < row['estoque_minimo']: return '🔴 Crítico'
            if row['Runway'] != 999 and row['Runway'] <= row['lead_time']: return '🟠 Risco'
            return '🟢 OK'
        df_context['Status'] = df_context.apply(set_status, axis=1)

        posicao_estoque_md = df_context[['Status', 'categoria', 'nome', 'saldo_atual', 'estoque_minimo', 'valor_unitario', 'Cobertura']].to_markdown(index=False)
        movimentacoes_recente_md = recente_movs.to_markdown(index=False) if not recente_movs.empty else "Nenhuma movimentação física registrada no WMS."

        # Contexto operacional injetado na IA
        system_context = f"""
        Você é o Analista Inteligente Sênior do WMS 4.0, responsável pela gestão e auditoria de um Almoxarifado Interno de Insumos (uso interno).
        Seu objetivo é auxiliar o operador a planejar compras, gerenciar coberturas, detectar riscos de rupturas de estoque e responder dúvidas de auditoria com precisão absoluta.
        
        Abaixo está o retrato em tempo real do sistema:
        
        POSIÇÃO DE ESTOQUE ATUAL:
        {posicao_estoque_md}
        
        ÚLTIMAS 10 MOVIMENTAÇÕES DE REGISTRO HISTÓRICO:
        {movimentacoes_recente_md}
        
        Instruções de resposta:
        1. Responda em bom português brasileiro, de forma clara, altamente analítica e concisa.
        2. Utilize tabelas em markdown e marcadores para estruturar recomendações logísticas.
        3. Você está dialogando diretamente com um operador interno do WMS. Seja prestativo e profissional.
        """

        # --- AÇÕES RÁPIDAS (BOTOEIRA DE DIAGNÓSTICO) ---
        gerar_diagnostico = st.button("✨ Girar Diagnóstico Logístico Completo", type="secondary", use_container_width=True)
        
        if gerar_diagnostico:
            st.session_state["gemini_chat_history"].append({"role": "user", "content": "Gere um Diagnóstico Logístico Completo do Almoxarifado."})
            with st.chat_message("user"):
                st.write("Gere um Diagnóstico Logístico Completo do Almoxarifado.")
                
            with st.chat_message("assistant"):
                with st.spinner("Analisando dados logísticos..."):
                    prompt = f"""
                    {system_context}
                    
                    Por favor, elabore um Diagnóstico Logístico estratégico contendo:
                    1. **Análise de Saúde do Estoque**: Uma visão do estado geral do inventário.
                    2. **Riscos Imediatos**: Alertas sobre itens em ruptura (saldo zero) ou críticos (abaixo do mínimo de segurança) antes de estourar o lead time.
                    3. **Plano de Ação de Reposição**: Recomendações urgentes de compras com quantidades sugeridas.
                    """
                    mod = genai.GenerativeModel(modelo_selecionado)
                    resposta = mod.generate_content(prompt).text
                    st.markdown(resposta)
                    st.session_state["gemini_chat_history"].append({"role": "assistant", "content": resposta})
            st.rerun()

        # --- CHAT INPUT ---
        user_query = st.chat_input("Pergunte algo sobre estoque ou reposição...")

        if user_query:
            st.session_state["gemini_chat_history"].append({"role": "user", "content": user_query})
            with st.chat_message("user"):
                st.markdown(user_query)

            with st.chat_message("assistant"):
                with st.spinner("O Analista IA está respondendo..."):
                    # Compila histórico da conversa para manter a coerência do chat
                    historico_conversa = ""
                    for msg in st.session_state["gemini_chat_history"][-6:-1]: # Pega as últimas 5 mensagens
                        role_label = "Operador" if msg["role"] == "user" else "Analista WMS"
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
