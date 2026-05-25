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
        
    st.divider()

    # --- PAINEL DE GERENCIAMENTO DE USUÁRIOS ATIVOS ---
    st.markdown("### 👥 Gerenciamento de Usuários Ativos")
    with get_conn() as conn:
        ativos = pd.read_sql("SELECT usuario, perfil FROM usuarios WHERE aprovado = 1", conn)
    
    if not ativos.empty:
        col_u1, col_u2, col_u3 = st.columns([2, 2, 2])
        with col_u1:
            usr_editar = st.selectbox("Selecione o usuário para gerenciar:", list(ativos["usuario"]), key="usr_edit")
        
        perfil_atual_db = ativos[ativos['usuario'] == usr_editar]['perfil'].values[0]
        idx_perfil = 0 if perfil_atual_db == "Operador" else 1

        with col_u2:
            novo_perfil = st.selectbox("Novo Nível de Acesso:", ["Operador", "Administrador"], index=idx_perfil, key="perf_edit")
        with col_u3:
            st.write("") 
            if st.button("🔄 Atualizar Perfil", use_container_width=True):
                if usr_editar == st.session_state["usuario_atual"] and novo_perfil == "Operador":
                    st.error("⚠️ Operação bloqueada! Você não pode rebaixar a própria conta para evitar perder o acesso à aba de Configurações.")
                else:
                    with get_conn() as conn:
                        conn.execute("UPDATE usuarios SET perfil = ? WHERE usuario = ?", (novo_perfil, usr_editar))
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Alterar Perfil", f"Perfil do operador '{usr_editar}' alterado para '{novo_perfil}'.")
                    disparar_sincronizacao()
                    st.success(f"Perfil de '{usr_editar}' atualizado para {novo_perfil} com sucesso!")
                    st.rerun()
                    
    st.divider()

    # --- CRUD DE PRODUTOS ---
    st.subheader("🛠️ Catálogo de Insumos")
    a1, a2, a3 = st.tabs(["➕ Novo Insumo", "✏️ Editar Insumo", "🗑️ Excluir Insumo"])
    
    with a1:
        with st.form("new_p"):
            n = st.text_input("Nome do Insumo")
            c = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
            m = st.number_input("Mínimo", value=10)
            l = st.number_input("Lead Time (Dias)", value=3)
            v = st.number_input("Valor Inicial Un. (R$)", value=0.0)
            if st.form_submit_button("Cadastrar"):
                if n.strip():
                    cadastrar_produto(n.strip(), m, v, c, l)
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Cadastrar Insumo", f"Insumo '{n.strip()}' cadastrado. Setor: {c}, Mínimo: {m}, Preço: R$ {v:.2f}")
                    disparar_sincronizacao()
                    st.toast(f"➕ Cadastrado!", icon="✨")
                    st.rerun()
                
    with a2:
        if not df.empty:
            op_e = dict(zip(df["nome"], df["id"]))
            s_e = st.selectbox("Produto p/ Editar", list(op_e.keys()))
            id_e = op_e[s_e]
            p_at = df[df["id"]==id_e].iloc[0]
            with st.form("edit_p"):
                en = st.text_input("Nome", value=p_at["nome"])
                ec = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
                em = st.number_input("Mínimo", value=int(p_at["estoque_minimo"]))
                el = st.number_input("Lead Time", value=int(p_at["lead_time"]))
                ev = st.number_input("Preço Médio", value=float(p_at["valor_unitario"]))
                if st.form_submit_button("Atualizar"):
                    editar_produto(id_e, en, em, ev, ec, el)
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Editar Insumo", f"Insumo ID {id_e} editado. Novo Nome: '{en}', Setor: {ec}, Mínimo: {em}, Preço: R$ {ev:.2f}")
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

    # --- HISTÓRICO GERAL DE AUDITORIA ---
    st.divider()
    st.markdown("### 📜 Histórico Geral de Auditoria WMS")
    with get_conn() as conn:
        logs_df = pd.read_sql("SELECT data_hora AS [Data/Hora], usuario AS [Operador], acao AS [Ação], detalhes AS [Detalhes] FROM logs_auditoria ORDER BY id DESC", conn)
    
    if not logs_df.empty:
        st.dataframe(logs_df, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma ação administrativa registrada no histórico de auditoria ainda.")
