import streamlit as st
from database.connection import get_conn
from utils.drive_sync import disparar_sincronizacao
from database.queries import registrar_log_auditoria, registrar_entrada_produto, registrar_saida_produto
from utils.backup import realizar_backup_local

def render_operations_ui(df):
    st.subheader("⬇️ Registrar Entrada ou 📤 Registrar Saída")
    if df.empty:
        st.info("Nenhum insumo disponível para lançamentos de entrada ou saída.")
        return

    col_e, col_s = st.columns(2)
    with col_e:
        with st.container(border=True):
            st.subheader("⬇️ Registrar Entrada")
            ops = dict(zip(df["nome"], df["id"]))
            sel_e = st.selectbox("Produto", list(ops.keys()), key="e_p")
            id_pe = ops[sel_e]
            p_atual = df.loc[df["id"]==id_pe].iloc[0]
            sal_e = int(p_atual["saldo_atual"])
            pmp_antigo = float(p_atual["valor_unitario"])
            
            c1, c2 = st.columns([1, 1])
            with c1: qe = st.number_input("Quantidade", min_value=1, key="e_q")
            with c2: preco_compra = st.number_input("Preço Unit. de Compra (R$)", min_value=0.0, value=pmp_antigo, step=0.01, key="e_v")
            obs_e = st.text_input("Nota/Fornecedor", key="e_obs")
                
            if st.button("Confirmar Entrada", type="secondary"):
                sucesso_ent, msg_ent = registrar_entrada_produto(id_pe, qe, preco_compra, obs_e)
                if sucesso_ent:
                    detalhes_log = f"Registrou entrada de {qe} un. do insumo '{sel_e}' (Preço Pago: R$ {preco_compra:.2f}/un; Total: R$ {qe * preco_compra:.2f})."
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Entrada de Estoque", detalhes_log)

                    realizar_backup_local()
                    disparar_sincronizacao()
                    st.toast("📥 Entrada registrada com sucesso!", icon="✅")
                    st.rerun()
                else:
                    st.error(f"Erro ao registrar entrada: {msg_ent}")

    with col_s:
        with st.container(border=True):
            st.subheader("📤 Registrar Saída")
            ops = dict(zip(df["nome"], df["id"]))
            sel = st.selectbox("Produto ", list(ops.keys()), key="s_p")
            id_p = ops[sel]
            
            p_atual_s = df.loc[df["id"]==id_p].iloc[0]
            max_s = int(p_atual_s["saldo_atual"])
            est_min_s = int(p_atual_s["estoque_minimo"])
            
            c1, c2 = st.columns([1, 2])
            with c1: q = st.number_input("Quantidade", min_value=1, key="s_q")
            with c2: obs_s = st.text_input("Observação/Destino", key="s_obs")
            
            saldo_futuro = max_s - q
            bloquear_saida = q > max_s
            
            # --- VALIDAÇÕES E ALERTAS DINÂMICOS PREVENTIVOS ---
            if bloquear_saida:
                st.error(f"❌ Estoque Insuficiente! Saldo na prateleira: {max_s} un.")
            elif saldo_futuro == 0:
                st.warning(f"⚠️ Atenção! Esta retirada irá ZERAR o saldo físico deste insumo em estoque!")
            elif saldo_futuro < est_min_s:
                st.warning(f"⚠️ Alerta! Esta retirada deixará o saldo ({saldo_futuro} un) ABAIXO do estoque mínimo de segurança ({est_min_s} un)!")
            else:
                st.success(f"🟢 Saldo seguro após retirada: {saldo_futuro} un (Estoque Mínimo: {est_min_s} un).")
                
            if st.button("Confirmar Saída", type="primary", disabled=bloquear_saida):
                sucesso_saida, msg_saida = registrar_saida_produto(id_p, q, obs_s)
                if sucesso_saida:
                    detalhes_log = f"Registrou saída de {q} un. do insumo '{sel}' (Observação: '{obs_s}'). Saldo restante estimado: {max_s - q} un."
                    registrar_log_auditoria(st.session_state["usuario_atual"], "Saída de Estoque", detalhes_log)

                    realizar_backup_local()
                    disparar_sincronizacao()
                    st.toast("📤 Baixa realizada com sucesso!", icon="🚀")
                    st.rerun()
                else:
                    st.error(f"Erro ao registrar saída: {msg_saida}")
