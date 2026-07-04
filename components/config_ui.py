import pandas as pd
import streamlit as st
from database.connection import get_conn
from database.queries import cadastrar_produto, editar_produto, deletar_produto, registrar_log_auditoria
from utils.drive_sync import disparar_sincronizacao

def render_config_ui(df):
    st.markdown("### 👑 Painel de Aprovações de Novos Operadores")
    with get_conn() as conn:
        pendentes = pd.read_sql("SELECT usuario, pergunta_seguranca FROM usuarios WHERE aprovado = 0", conn)
    
    if not pendentes.empty:
        st.dataframe(pendentes, use_container_width=True, hide_index=True)
        col_sel, col_perf, col_act = st.columns([2, 2, 2])
        with col_sel:
            usr_alvo = st.selectbox("Selecione o usuário:", list(pendentes["usuario"]))
        with col_perf:
            perfil_alvo = st.selectbox("Nível de Acesso:", ["Operador", "Administrador"])
        with col_act:
            c_ap, c_rec = st.columns(2)
            with c_ap:
                if st.button("✅ Aprovar", use_container_width=True):
                    with get_conn() as conn:
                        conn.execute("UPDATE usuarios SET aprovado = 1, perfil = ? WHERE usuario = ?", (perfil_alvo, usr_alvo))
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Aprovar Operador", f"Operador '{usr_alvo}' aprovado com perfil '{perfil_alvo}'.")
                    disparar_sincronizacao()
                    st.success(f"Operador '{usr_alvo}' liberado como {perfil_alvo}!")
                    st.rerun()
            with c_rec:
                if st.button("❌ Recusar", use_container_width=True):
                    with get_conn() as conn:
                        conn.execute("DELETE FROM usuarios WHERE usuario = ?", (usr_alvo,))
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Recusar Operador", f"Solicitação de cadastro do operador '{usr_alvo}' recusada.")
                    disparar_sincronizacao()
                    st.warning(f"A solicitação de '{usr_alvo}' foi excluída.")
                    st.rerun()
    else:
        st.success("✅ Nenhuma solicitação de cadastro pendente na fila.")
           # --- PAINEL DE GERENCIAMENTO DE USUÁRIOS ATIVOS ---
    st.markdown("### 👥 Gerenciamento de Usuários Cadastrados")
    with get_conn() as conn:
        ativos = pd.read_sql("SELECT usuario, perfil, aprovado FROM usuarios WHERE aprovado IN (1, 2)", conn)
    
    if not ativos.empty:
        # Formata para visualização amigável no selectbox
        def format_usr_opt(usr):
            row = ativos[ativos["usuario"] == usr].iloc[0]
            status_txt = "🟢 Ativo" if row["aprovado"] == 1 else "🔴 Suspenso"
            return f"{usr} ({row['perfil']} - {status_txt})"
            
        col_u1, col_u2, col_u3, col_u4 = st.columns([2, 1.5, 1.5, 1.5])
        with col_u1:
            usr_editar = st.selectbox("Selecione o usuário para gerenciar:", list(ativos["usuario"]), key="usr_edit", format_func=format_usr_opt)
        
        user_row = ativos[ativos['usuario'] == usr_editar].iloc[0]
        perfil_atual_db = user_row['perfil']
        status_atual_db = user_row['aprovado']
        
        idx_perfil = 0 if perfil_atual_db == "Operador" else 1
        idx_status = 0 if status_atual_db == 1 else 1

        with col_u2:
            novo_perfil = st.selectbox("Novo Nível de Acesso:", ["Operador", "Administrador"], index=idx_perfil, key="perf_edit")
        with col_u3:
            novo_status = st.selectbox("Status da Conta:", ["Ativo", "Suspenso"], index=idx_status, key="status_edit")
        with col_u4:
            st.write("") 
            if st.button("🔄 Salvar Alterações", use_container_width=True):
                status_num = 1 if novo_status == "Ativo" else 2
                
                if usr_editar.lower() == st.session_state["usuario_atual"].lower():
                    if novo_perfil == "Operador" or status_num == 2:
                        st.error("⚠️ Operação bloqueada! Você não pode suspender ou rebaixar sua própria conta para evitar bloqueios acidentais.")
                    else:
                        with get_conn() as conn:
                            conn.execute("UPDATE usuarios SET perfil = ?, aprovado = ? WHERE usuario = ?", (novo_perfil, status_num, usr_editar))
                        st.success("Configurações atualizadas!")
                        st.rerun()
                else:
                    with get_conn() as conn:
                        conn.execute("UPDATE usuarios SET perfil = ?, aprovado = ? WHERE usuario = ?", (novo_perfil, status_num, usr_editar))
                    
                    status_log_txt = "Ativo" if status_num == 1 else "Suspenso"
                    detalhe_log = f"Perfil de '{usr_editar}' atualizado para '{novo_perfil}' e Status para '{status_log_txt}'."
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Gerenciamento de Operador", detalhe_log)
                    
                    disparar_sincronizacao()
                    st.success(f"Alterações salvas para '{usr_editar}' com sucesso!")
                    st.rerun()
                    
    st.divider()

    # --- CRUD DE PRODUTOS ---
    st.subheader("🛠️ Catálogo de Insumos")
    a1, a2, a3 = st.tabs(["➕ Novo Insumo", "✏️ Editar Insumo", "🗑️ Excluir Insumo"])
    
    with a1:
        with st.form("new_p"):
            n = st.text_input("Nome do Insumo")
            c = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
            m = st.number_input("Mínimo", value=10, min_value=0)
            l = st.number_input("Lead Time (Dias)", value=3, min_value=0)
            v = st.number_input("Valor Inicial Un. (R$)", value=0.0, min_value=0.0)
            crit = st.selectbox("Criticidade (XYZ)", ["X (Baixa)", "Y (Média)", "Z (Crítica/Vital)"], index=1)
            if st.form_submit_button("Cadastrar"):
                if n.strip():
                    cadastrar_produto(n.strip(), m, v, c, l, crit[0])
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Cadastrar Insumo", f"Insumo '{n.strip()}' cadastrado. Setor: {c}, Mínimo: {m}, Preço: R$ {v:.2f}, Criticidade: {crit[0]}")
                    disparar_sincronizacao()
                    st.toast(f"➕ Cadastrado!", icon="✨")
                    st.rerun()
                
    with a2:
        if not df.empty:
            op_e = dict(zip(df["nome"], df["id"]))
            s_e = st.selectbox("Produto p/ Editar", list(op_e.keys()))
            id_e = op_e[s_e]
            p_at = df[df["id"]==id_e].iloc[0]
            p_crit_db = p_at.get("criticidade", "Y")
            idx_crit = 0 if p_crit_db == 'X' else (1 if p_crit_db == 'Y' else 2)
            
            with st.form("edit_p"):
                en = st.text_input("Nome", value=p_at["nome"])
                ec = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
                em = st.number_input("Mínimo", value=int(p_at["estoque_minimo"]), min_value=0)
                el = st.number_input("Lead Time", value=int(p_at["lead_time"]), min_value=0)
                ev = st.number_input("Preço Médio", value=float(p_at["valor_unitario"]), min_value=0.0)
                ecrit = st.selectbox("Criticidade (XYZ)", ["X (Baixa)", "Y (Média)", "Z (Crítica/Vital)"], index=idx_crit)
                if st.form_submit_button("Atualizar"):
                    editar_produto(id_e, en, em, ev, ec, el, ecrit[0])
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Editar Insumo", f"Insumo ID {id_e} editado. Novo Nome: '{en}', Setor: {ec}, Mínimo: {em}, Preço: R$ {ev:.2f}, Criticidade: {ecrit[0]}")
                    disparar_sincronizacao()
                    st.toast(f"✏️ Atualizado!", icon="⚙️")
                    st.rerun()
                    
    with a3:
        if not df.empty:
            op_d = dict(zip(df["nome"], df["id"]))
            s_d = st.selectbox("Selecione para Excluir", list(op_d.keys()))
            id_d = op_d[s_d]
            confirmar = st.checkbox("Confirmo que pretendo apagar este insumo e destruir seu histórico.")
            if st.button("🗑️ Eliminar Definitivamente", type="primary", disabled=not confirmar):
                try:
                    deletar_produto(id_d)
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Excluir Insumo", f"Insumo '{s_d}' (ID {id_d}) excluído definitivamente junto com o histórico.")
                    disparar_sincronizacao()
                    st.toast(f"🗑️ Removido!", icon="🗑️")
                    st.rerun()
                except Exception as e: 
                    st.error(f"Erro: {e}")

    # --- INTEGRAÇÃO COM GOOGLE DRIVE (NUVEM) ---
    st.divider()
    st.markdown("### ☁️ Integração com Google Drive (Nuvem)")
    
    with get_conn() as conn:
        row_cfg = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'drive_sync_ativo'").fetchone()
        sync_atual = (row_cfg[0] == '1') if row_cfg else True
        
    col_tgl, col_btn1, col_btn2 = st.columns([2, 2, 2])
    with col_tgl:
        novo_sync = st.toggle("Sincronizar em segundo plano", value=sync_atual)
    with col_btn1:
        if st.button("📤 Enviar para Nuvem", type="secondary", use_container_width=True):
            from datetime import datetime
            import threading
            from utils.drive_sync import executar_sincronizacao_drive
            
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                    ("Sincronização manual em execução...", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
            
            # Executa a sincronização em segundo plano usando Thread
            threading.Thread(target=executar_sincronizacao_drive).start()
            registrar_log_auditoria(st.session_state["usuario_atual"], "Sincronização Manual", "Disparou upload manual com o Google Drive.")
            st.toast("Nuvem: Upload iniciado!", icon="☁️")
            st.success("Sincronização de envio iniciada!")
            st.rerun()
            
    with col_btn2:
        if st.button("📥 Baixar da Nuvem", type="primary", use_container_width=True):
            from utils.drive_sync import descarregar_do_drive
            with st.spinner("Baixando base de dados do Drive..."):
                sucesso = descarregar_do_drive()
            if sucesso:
                registrar_log_auditoria(st.session_state["usuario_atual"], "Restaurar Backup Nuvem", "Forçou download manual do banco de dados do Google Drive.")
                st.toast("Nuvem: Banco baixado com sucesso!", icon="☁️")
                st.success("Banco de dados baixado e atualizado na tela!")
                st.rerun()
            else:
                st.error("Erro ao baixar o banco da nuvem. Verifique o status na barra lateral.")
    
    if novo_sync != sync_atual:
        from datetime import datetime
        valor_str = '1' if novo_sync else '0'
        msg_aud = "Ativou a sincronização de nuvem." if novo_sync else "Desativou a sincronização de nuvem."
        with get_conn() as conn:
            conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'drive_sync_ativo'", (valor_str,))
            if not novo_sync:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                    ("Sincronização na nuvem desativada localmente.", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                    ("Sincronização na nuvem ativada.", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
        registrar_log_auditoria(st.session_state["usuario_atual"], "Alterar Configuração Nuvem", msg_aud)
        st.success("Configuração de nuvem atualizada!")
        if novo_sync:
            disparar_sincronizacao()
        st.rerun()

    # --- AJUSTE DE FATORES DE SEGURANÇA POR SETOR ---
    st.divider()
    st.markdown("### 🎯 Margens de Segurança por Setor (Estoque Mínimo)")
    st.caption("Parametrizador dos multiplicadores de cobertura (Fatores de Segurança) utilizados para calcular o Estoque Mínimo Ideal e sugestões de compra de cada setor.")
    
    setores_sistema = ["Limpeza", "Copa", "EPI", "Escritório", "Geral"]
    fatores_atuais = {}
    
    with get_conn() as conn:
        rows_f = conn.execute("SELECT chave, valor FROM configuracoes WHERE chave LIKE 'fator_seguranca_%'").fetchall()
        fatores_atuais = {r[0]: float(r[1]) for r in rows_f}
        
    padroes = {"Limpeza": 1.1, "Copa": 1.1, "EPI": 1.2, "Escritório": 1.1, "Geral": 1.1}
    for s in setores_sistema:
        key = f"fator_seguranca_{s}"
        if key not in fatores_atuais:
            fatores_atuais[key] = padroes[s]
            try:
                with get_conn() as conn:
                    conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES (?, ?)", (key, str(padroes[s])))
            except Exception:
                pass
                
    with st.form("form_fatores_setor"):
        col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
        with col_s1:
            fat_limp = st.number_input("Limpeza:", min_value=1.0, max_value=2.5, value=fatores_atuais["fator_seguranca_Limpeza"], step=0.1)
        with col_s2:
            fat_copa = st.number_input("Copa:", min_value=1.0, max_value=2.5, value=fatores_atuais["fator_seguranca_Copa"], step=0.1)
        with col_s3:
            fat_epi = st.number_input("EPI:", min_value=1.0, max_value=2.5, value=fatores_atuais["fator_seguranca_EPI"], step=0.1)
        with col_s4:
            fat_escr = st.number_input("Escritório:", min_value=1.0, max_value=2.5, value=fatores_atuais["fator_seguranca_Escritório"], step=0.1)
        with col_s5:
            fat_geral = st.number_input("Geral:", min_value=1.0, max_value=2.5, value=fatores_atuais["fator_seguranca_Geral"], step=0.1)
            
        if st.form_submit_button("💾 Salvar Fatores de Segurança por Setor", type="primary", use_container_width=True):
            try:
                with get_conn() as conn:
                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'fator_seguranca_Limpeza'", (str(fat_limp),))
                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'fator_seguranca_Copa'", (str(fat_copa),))
                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'fator_seguranca_EPI'", (str(fat_epi),))
                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'fator_seguranca_Escritório'", (str(fat_escr),))
                    conn.execute("UPDATE configuracoes SET valor = ? WHERE chave = 'fator_seguranca_Geral'", (str(fat_geral),))
                
                detalhe_log = (f"Atualizou fatores de segurança dos setores: Limpeza={fat_limp}, Copa={fat_copa}, "
                               f"EPI={fat_epi}, Escritório={fat_escr}, Geral={fat_geral}.")
                registrar_log_auditoria(st.session_state["usuario_atual"], "Ajustar Cobertura Setores", detalhe_log)
                
                disparar_sincronizacao()
                st.toast("Fatores salvos e sincronizados!", icon="✅")
                st.success("Margens de segurança por setor salvas com sucesso!")
                st.rerun()
            except Exception as e_fat:
                st.error(f"Erro ao salvar configurações de margens: {e_fat}")

    # ─────────────────────────────────────────────────────────────
    # CENTRAL DE EXPORTAÇÕES PREMIUM WMS 5.0
    # ─────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 📥 Central de Relatórios Premium WMS 5.0")
    st.caption("Gere e exporte planilhas profissionais no formato Excel (.xlsx) e relatórios executivos em alta definição formatados para impressão em PDF.")
    
    from utils.reports import gerar_excel_estoque, gerar_excel_movimentacoes, gerar_excel_auditoria, gerar_html_pdf_estoque
    from database.queries import listar_movimentacoes
    from datetime import datetime
    
    col_rep1, col_rep2 = st.columns(2)
    
    with col_rep1:
        st.markdown("**📊 Planilhas Analíticas Excel (.xlsx)**")
        # 1. Estoque Atual (Valuation)
        try:
            excel_estoque_bytes = gerar_excel_estoque(df)
            st.download_button(
                label="📥 Baixar Posição de Estoque & Valuation (.xlsx)",
                data=excel_estoque_bytes,
                file_name=f"valuation_estoque_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        except Exception as e_rep:
            st.error(f"Erro ao gerar relatório de estoque: {e_rep}")
            
        # 2. Extrato de Movimentações
        try:
            mv_df = listar_movimentacoes()
            excel_movs_bytes = gerar_excel_movimentacoes(mv_df)
            st.download_button(
                label="📥 Baixar Extrato de Movimentações (.xlsx)",
                data=excel_movs_bytes,
                file_name=f"extrato_movimentacoes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        except Exception as e_rep:
            st.error(f"Erro ao gerar extrato: {e_rep}")
            
        # 3. Logs de Auditoria
        try:
            with get_conn() as conn:
                logs_raw = pd.read_sql("SELECT * FROM logs_auditoria ORDER BY id DESC", conn)
            excel_logs_bytes = gerar_excel_auditoria(logs_raw)
            st.download_button(
                label="📥 Baixar Auditoria Geral de Logs (.xlsx)",
                data=excel_logs_bytes,
                file_name=f"auditoria_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        except Exception as e_rep:
            st.error(f"Erro ao gerar logs: {e_rep}")
            
    with col_rep2:
        st.markdown("**📄 Relatório Executivo PDF/Imprimir**")
        st.write("Compila a posição completa de estoque e as 10 movimentações recentes em um layout premium de folha A4 com seções de assinatura técnica de auditoria.")
        
        try:
            mv_df_head = listar_movimentacoes()
            with get_conn() as conn:
                logs_raw = pd.read_sql("SELECT * FROM logs_auditoria ORDER BY id DESC", conn)
            html_report = gerar_html_pdf_estoque(df, mv_df_head, logs_raw)
            
            st.download_button(
                label="📥 Baixar Relatório Executivo Premium (HTML/PDF)",
                data=html_report,
                file_name=f"relatorio_executivo_wms_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                mime="text/html",
                use_container_width=True,
                type="primary"
            )
            st.caption("💡 *Ao abrir o arquivo baixado no navegador, aperte **Ctrl+P** (ou Cmd+P no Mac) para salvá-lo como um PDF profissional diagramado em formato A4.*")
        except Exception as e_rep:
            st.error(f"Erro ao compilar PDF HTML: {e_rep}")
 
    # --- ARQUIVAMENTO E LIMPEZA DE LOGS DE AUDITORIA ---
    st.divider()
    st.markdown("### 📦 Arquivamento e Limpeza de Histórico de Logs")
    st.caption("Remova logs antigos para economizar espaço em disco. O sistema gerará um download em CSV do histórico arquivado para compliance.")
    
    col_arq1, col_arq2 = st.columns([2, 4])
    with col_arq1:
        dias_arquivar = st.number_input("Arquivar logs mais antigos que (dias):", min_value=1, value=90, step=1, key="arq_days")
    with col_arq2:
        st.write("")
        st.write("")
        with st.popover("⚠️ Executar Arquivamento de Histórico", use_container_width=True):
            st.warning("Esta ação removerá permanentemente os logs selecionados do banco de dados local. Certifique-se de baixar o arquivo CSV gerado abaixo.")
            btn_confirmar_arq = st.button("Confirmar Limpeza e Gerar Download", type="primary", use_container_width=True)

    if 'csv_arquivado' not in st.session_state:
        st.session_state['csv_arquivado'] = None
    if 'total_arquivado' not in st.session_state:
        st.session_state['total_arquivado'] = 0

    if btn_confirmar_arq:
        from database.queries import arquivar_logs_antigos
        sucesso, conteudo, total_del = arquivar_logs_antigos(dias_arquivar)
        if sucesso:
            st.session_state['csv_arquivado'] = conteudo
            st.session_state['total_arquivado'] = total_del
            registrar_log_auditoria(st.session_state["usuario_atual"], "Arquivamento de Logs", f"Limpeza de logs anteriores a {dias_arquivar} dias. Total de registros removidos: {total_del}.")
            disparar_sincronizacao()
            st.toast(f"📦 {total_del} logs arquivados com sucesso!", icon="✅")
        else:
            st.error(f"Erro: {conteudo}")

    if st.session_state['csv_arquivado'] is not None:
        st.success(f"🎉 Concluído! **{st.session_state['total_arquivado']}** logs antigos foram removidos do banco.")
        st.download_button(
            label="📥 Baixar Histórico Arquivado (.csv)",
            data=st.session_state['csv_arquivado'],
            file_name=f"arquivamento_logs_wms_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )

    # --- HISTÓRICO GERAL DE AUDITORIA ---
    st.divider()
    st.markdown("### 📜 Painel de Auditoria e Histórico Geral de Operações")
    st.caption("Filtragem avançada de todas as movimentações e ações de segurança executadas pelos operadores no WMS.")
    
    with get_conn() as conn:
        try:
            logs_df = pd.read_sql("SELECT data_hora AS [Data/Hora], usuario AS [Operador], acao AS [Ação], detalhes AS [Detalhes], ip AS [IP], user_agent AS [Navegador] FROM logs_auditoria ORDER BY id DESC", conn)
        except Exception:
            logs_df = pd.read_sql("SELECT data_hora AS [Data/Hora], usuario AS [Operador], acao AS [Ação], detalhes AS [Detalhes] FROM logs_auditoria ORDER BY id DESC", conn)
    
    if not logs_df.empty:
        # Colunas de Filtro
        col_filtro1, col_filtro2 = st.columns([1, 1])
        
        with col_filtro1:
            busca = st.text_input("🔍 Buscar no histórico (Operador, Detalhes, IP):", "").strip()
            
        with col_filtro2:
            acoes_disponiveis = sorted(list(logs_df["Ação"].unique()))
            filtro_acoes = st.multiselect("🏷️ Filtrar por Ações:", acoes_disponiveis, default=[])
            
        # Aplicação dos Filtros
        df_filtrado = logs_df.copy()
        
        if busca:
            filtro_busca = (
                df_filtrado["Operador"].str.contains(busca, case=False, na=False) |
                df_filtrado["Detalhes"].str.contains(busca, case=False, na=False)
            )
            if "IP" in df_filtrado.columns:
                filtro_busca = filtro_busca | df_filtrado["IP"].str.contains(busca, case=False, na=False)
            if "Navegador" in df_filtrado.columns:
                filtro_busca = filtro_busca | df_filtrado["Navegador"].str.contains(busca, case=False, na=False)
            df_filtrado = df_filtrado[filtro_busca]
            
        if filtro_acoes:
            df_filtrado = df_filtrado[df_filtrado["Ação"].isin(filtro_acoes)]
            
        # Estilização Condicional das Linhas
        def destacar_acoes(row):
            acao = row['Ação']
            color = ''
            if acao in ['Entrada de Estoque', 'Aprovar Operador']:
                color = 'background-color: rgba(16, 185, 129, 0.08); color: #10b981; font-weight: bold;'
            elif acao in ['Saída de Estoque', 'Excluir Insumo', 'Recusar Operador']:
                color = 'background-color: rgba(239, 68, 68, 0.08); color: #ef4444; font-weight: bold;'
            elif acao in ['Ajuste de Inventário']:
                color = 'background-color: rgba(245, 158, 11, 0.08); color: #f59e0b; font-weight: bold;'
            elif 'Senha' in acao or 'Login' in acao or 'Logoff' in acao:
                color = 'background-color: rgba(59, 130, 246, 0.08); color: #3b82f6;'
            else:
                color = 'color: #94a3b8;'
            return [color if col == 'Ação' else '' for col in row.index]

        if not df_filtrado.empty:
            styled_df = df_filtrado.style.apply(destacar_acoes, axis=1)
            st.dataframe(styled_df, use_container_width=True, hide_index=True)
            st.caption(f"Exibindo {len(df_filtrado)} de {len(logs_df)} registros encontrados.")
        else:
            st.warning("⚠️ Nenhum registro encontrado para os filtros aplicados.")
    else:
        st.info("Nenhuma ação registrada no histórico de auditoria ainda.")
