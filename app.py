import streamlit as st
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from io import BytesIO
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA E CONSTANTES
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Controle de Estoque",
    page_icon="📦",
    layout="wide",
)

DB_PATH = "estoque.db"
FOLDER_ID = st.secrets["FOLDER_ID"]

# ─────────────────────────────────────────────────────────────
# CONEXÃO E SINCRONIZAÇÃO COM GOOGLE DRIVE
# ─────────────────────────────────────────────────────────────
def obter_servico_drive():
    info_chaves = dict(st.secrets["gcp_service_account"])
    credenciais = service_account.Credentials.from_service_account_info(info_chaves)
    return build('drive', 'v3', credentials=credenciais)

def descarregar_do_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false"
        resultados = servico.files().list(q=query, fields="files(id)").execute()
        ficheiros = resultados.get('files', [])
        
        if ficheiros:
            id_ficheiro = ficheiros[0]['id']
            requisicao = servico.files().get_media(fileId=id_ficheiro)
            with open(DB_PATH, "wb") as f:
                carregador = MediaIoBaseDownload(f, requisicao)
                concluido = False
                while not concluido:
                    _, concluido = carregador.next_chunk()
            return True
    except Exception as e:
        st.error(f"Erro ao descarregar do Google Drive: {e}")
    return False

def enviar_para_o_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false"
        resultados = servico.files().list(q=query, fields="files(id)").execute()
        ficheiros = resultados.get('files', [])
        
        metadados = {'name': DB_PATH, 'parents': [FOLDER_ID]}
        media = MediaFileUpload(DB_PATH, mimetype='application/x-sqlite3', resumable=True)
        
        if ficheiros:
            id_ficheiro = ficheiros[0]['id']
            servico.files().update(fileId=id_ficheiro, media_body=media).execute()
        else:
            servico.files().create(body=metadados, media_body=media).execute()
    except Exception as e:
        st.error(f"Erro ao enviar banco para o Drive: {e}")

def sincronizar_csv_drive(df, nome_arquivo):
    """Exporta um DataFrame como CSV para o Drive (Ponte para o Looker Studio/BI)."""
    try:
        servico = obter_servico_drive()
        query = f"name='{nome_arquivo}' and '{FOLDER_ID}' in parents and trashed=false"
        resultados = servico.files().list(q=query, fields="files(id)").execute()
        ficheiros = resultados.get('files', [])

        # Converte para CSV com padrão utf-8-sig para garantir a leitura perfeita de acentos
        csv_bytes = df.to_csv(index=False).encode('utf-8-sig')
        media = MediaIoBaseUpload(BytesIO(csv_bytes), mimetype='text/csv', resumable=True)
        metadados = {'name': nome_arquivo, 'parents': [FOLDER_ID]}

        if ficheiros:
            servico.files().update(fileId=ficheiros[0]['id'], media_body=media).execute()
        else:
            servico.files().create(body=metadados, media_body=media).execute()
    except Exception as e:
        st.error(f"Erro ao sincronizar CSV {nome_arquivo}: {e}")

def sincronizar_tudo():
    """Roda a sincronização do banco SQLite e a exportação dos CSVs de uma só vez."""
    enviar_para_o_drive()
    sincronizar_csv_drive(listar_produtos(), "produtos_looker.csv")
    sincronizar_csv_drive(listar_movimentacoes(), "movimentacoes_looker.csv")

# ─────────────────────────────────────────────────────────────
# FUNÇÕES DE EXPORTAÇÃO LOCAIS
# ─────────────────────────────────────────────────────────────
@st.cache_data
def converter_para_csv(df):
    return df.to_csv(index=False).encode("utf-8-sig")

def converter_para_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Movimentacoes")
    return output.getvalue()

# ─────────────────────────────────────────────────────────────
# BANCO DE DADOS E LÓGICA
# ─────────────────────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                saldo_atual INTEGER NOT NULL DEFAULT 0,
                estoque_minimo INTEGER DEFAULT 10,
                valor_unitario REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto INTEGER NOT NULL REFERENCES produtos(id),
                data_hora TEXT NOT NULL,
                tipo TEXT NOT NULL
                    CHECK(tipo IN ('Entrada','Saída','Ajuste','Contagem')),
                quantidade INTEGER NOT NULL,
                saldo_resultante INTEGER NOT NULL,
                observacao TEXT
            );
        """)
        cursor = conn.execute("SELECT COUNT(*) FROM produtos")
        if cursor.fetchone()[0] == 0:
            conn.executemany("""
                INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario)
                VALUES (?, ?, ?, ?)
            """, [
                ("Papel higiênico", 120, 50, 1.80),
                ("Sabonete líquido", 30, 10, 12.50),
                ("Desinfetante", 20, 10, 8.90),
                ("Saco de lixo", 80, 30, 0.90),
            ])

def listar_produtos():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)

def listar_movimentacoes():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, 
                   m.quantidade, m.saldo_resultante, m.observacao
            FROM movimentacoes m
            JOIN produtos p ON p.id = m.id_produto
            ORDER BY m.id DESC
        """, conn)

def atualizar_saldo(conn, id_produto, novo_saldo):
    conn.execute("UPDATE produtos SET saldo_atual = ? WHERE id = ?", (novo_saldo, id_produto))

def registrar_movimentacao(conn, id_produto, tipo, quantidade, saldo_resultante, obs):
    data_hora = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
    conn.execute("""
        INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (id_produto, data_hora, tipo, quantidade, saldo_resultante, obs))

def cadastrar_produto(nome, estoque_minimo, valor_unitario):
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario)
                VALUES (?, 0, ?, ?)
            """, (nome, estoque_minimo, valor_unitario))
        return True, "Produto cadastrado com sucesso."
    except sqlite3.IntegrityError:
        return False, "Produto já existe."

def deletar_produto(id_produto):
    with get_conn() as conn:
        conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
        conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))

# ─────────────────────────────────────────────────────────────
# INICIALIZAÇÃO CONTROLADA
# ─────────────────────────────────────────────────────────────
if "db_sincronizado" not in st.session_state:
    existe_no_drive = descarregar_do_drive()
    init_db()
    if not existe_no_drive:
        sincronizar_tudo()
    st.session_state["db_sincronizado"] = True

if "alerta_ruptura" not in st.session_state:
    st.session_state["alerta_ruptura"] = None

# ─────────────────────────────────────────────────────────────
# INTERFACE PRINCIPAL
# ─────────────────────────────────────────────────────────────
st.title("📦 Controle de Estoque")
st.caption("Controle logístico de insumos com Curva ABC e integração de BI")

if st.session_state["alerta_ruptura"]:
    st.warning(st.session_state["alerta_ruptura"], icon="🚨")
    st.session_state["alerta_ruptura"] = None

st.divider()

aba_painel, aba_entrada, aba_saida, aba_ajuste, aba_contagem, aba_historico, aba_cadastro = st.tabs([
    "📊 Painel", "⬇️ Entrada", "⬆️ Saída", "🔧 Ajuste", "📋 Contagem", "📜 Histórico", "➕ Produtos"
])

# ═════════════════════════════════════════════════════════════
# PAINEL
# ═════════════════════════════════════════════════════════════
with aba_painel:
    produtos_df = listar_produtos()
    movs_df = listar_movimentacoes()

    if not produtos_df.empty:
        produtos_df["valor_total"] = produtos_df["saldo_atual"] * produtos_df["valor_unitario"]
        
        # --- CÁLCULO DA CURVA ABC ---
        df_abc = produtos_df[produtos_df["valor_total"] > 0].copy()
        if not df_abc.empty:
            df_abc = df_abc.sort_values(by="valor_total", ascending=False)
            df_abc["perc"] = df_abc["valor_total"] / df_abc["valor_total"].sum()
            df_abc["perc_acumulado"] = df_abc["perc"].cumsum()

            def classificar_abc(perc):
                if perc <= 0.80: return 'A'
                elif perc <= 0.95: return 'B'
                else: return 'C'

            df_abc["Curva ABC"] = df_abc["perc_acumulado"].apply(classificar_abc)
            produtos_df = produtos_df.merge(df_abc[["id", "Curva ABC"]], on="id", how="left").fillna({"Curva ABC": "-"})
        else:
            produtos_df["Curva ABC"] = "-"
        # ----------------------------

        total_itens = len(produtos_df)
        total_saldo = int(produtos_df["saldo_atual"].sum())
        saldo_baixo = int((produtos_df["saldo_atual"] < produtos_df["estoque_minimo"]).sum())
        valor_total_estoque = produtos_df["valor_total"].sum()
        total_movs = len(movs_df)

        st.markdown("### Visão Geral")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Produtos", total_itens)
        c2.metric("Total estoque", total_saldo)
        c3.metric("Estoque crítico", saldo_baixo)
        c4.metric("Movimentações", total_movs)
        c5.metric("Valor estoque", f"R$ {valor_total_estoque:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.divider()
        col1, col2 = st.columns(2, gap="large")

        with col1:
            st.markdown("### 📦 Posição de Estoque")
            # Adicionada a coluna Classe na visualização
            st.dataframe(
                produtos_df[["Curva ABC", "nome", "saldo_atual", "estoque_minimo", "valor_unitario", "valor_total"]].rename(columns={
                    "Curva ABC": "Classe", "nome": "Produto", "saldo_atual": "Saldo", "estoque_minimo": "Mínimo", 
                    "valor_unitario": "Valor Un.", "valor_total": "Total"
                }),
                width="stretch", hide_index=True
            )

        with col2:
            st.markdown("### 🛒 Sugestão de Compras")
            with get_conn() as conn:
                df_compras = pd.read_sql("""
                    SELECT p.nome AS Produto, p.saldo_atual AS saldo_atual, p.estoque_minimo,
                           COALESCE(SUM(ABS(m.quantidade)), 0) AS consumo
                    FROM produtos p
                    LEFT JOIN movimentacoes m ON p.id = m.id_produto AND m.tipo = 'Saída'
                    GROUP BY p.id
                """, conn)

            df_compras["consumo_medio"] = df_compras["consumo"] / 4
            df_compras["Sugestão Compra"] = ((df_compras["consumo_medio"] * 4) + df_compras["estoque_minimo"] - df_compras["saldo_atual"]).clip(lower=0)

            st.dataframe(
                df_compras[["Produto", "consumo", "consumo_medio", "estoque_minimo", "saldo_atual", "Sugestão Compra"]].rename(columns={
                    "saldo_atual": "Saldo", "consumo": "Consumo", "consumo_medio": "Média/Sem", 
                    "estoque_minimo": "Mínimo", "Sugestão Compra": "Comprar"
                }),
                width="stretch", hide_index=True
            )

            df_pedido = df_compras[df_compras["Sugestão Compra"] > 0]
            if not df_pedido.empty:
                texto_pedido = "🛒 *Pedido de Insumos de Limpeza*\n\n"
                for index, row in df_pedido.iterrows():
                    texto_pedido += f"• {row['Produto']}: {int(row['Sugestão Compra'])} un\n"
                st.text_area("📝 Copiar pedido para WhatsApp/E-mail:", value=texto_pedido, height=120)
            else:
                st.success("✅ Estoque abastecido! Não há necessidade de compras no momento.")

        st.divider()
        col_graf1, col_graf2 = st.columns(2, gap="large")
        
        with col_graf1:
            st.markdown("### 📈 Histórico de Consumo")
            if not movs_df.empty:
                df_saidas = movs_df[movs_df["tipo"] == "Saída"].copy()
                if not df_saidas.empty:
                    df_saidas["Data"] = pd.to_datetime(df_saidas["data_hora"], format="%d/%m/%Y %H:%M").dt.date
                    consumo_tempo = df_saidas.groupby(["Data", "produto"])["quantidade"].sum().abs().reset_index()
                    consumo_pivot = consumo_tempo.pivot(index="Data", columns="produto", values="quantidade").fillna(0)
                    st.line_chart(consumo_pivot)
                else:
                    st.info("Registre saídas para visualizar o gráfico.")
            else:
                st.info("Ainda não há movimentações.")

        with col_graf2:
            st.markdown("### 📊 Produtos mais consumidos")
            if not movs_df.empty:
                saidas_df = movs_df[movs_df["tipo"] == "Saída"]
                if not saidas_df.empty:
                    grafico = saidas_df.groupby("produto")["quantidade"].sum().abs().sort_values(ascending=False)
                    st.bar_chart(grafico)
                else:
                    st.info("Registre saídas para gerar o gráfico.")
    else:
        st.info("Cadastre produtos para visualizar o painel.")

# ═════════════════════════════════════════════════════════════
# ENTRADA
# ═════════════════════════════════════════════════════════════
with aba_entrada:
    st.subheader("Registrar Entrada")
    produtos_df = listar_produtos()
    if produtos_df.empty:
        st.warning("⚠️ Nenhum produto cadastrado.")
    else:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="ent_prod")
        id_sel = opcoes[nome_sel]
        saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])

        col1, col2 = st.columns(2)
        with col1:
            qty = st.number_input("Quantidade", min_value=1, step=1, key="ent_qty")
        with col2:
            obs = st.text_input("Observação", key="ent_obs")

        st.info(f"Saldo atual: **{saldo_atual}** → Novo saldo: **{saldo_atual + int(qty)}**")

        if st.button("✅ Registrar Entrada", type="primary"):
            novo_saldo = saldo_atual + int(qty)
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo_saldo)
                registrar_movimentacao(conn, id_sel, "Entrada", int(qty), novo_saldo, obs)
            
            with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                sincronizar_tudo()
            
            st.toast("Entrada registrada e dados exportados!", icon="📥")
            st.rerun()

# ═════════════════════════════════════════════════════════════
# SAÍDA
# ═════════════════════════════════════════════════════════════
with aba_saida:
    st.subheader("Registrar Saída")
    produtos_df = listar_produtos()
    if produtos_df.empty:
        st.warning("⚠️ Nenhum produto cadastrado.")
    else:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="sai_prod")
        id_sel = opcoes[nome_sel]
        saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])
        estoque_min = int(produtos_df.loc[produtos_df["id"] == id_sel, "estoque_minimo"].values[0])

        col1, col2 = st.columns(2)
        with col1:
            qty = st.number_input("Quantidade", min_value=1, max_value=max(saldo_atual, 1), step=1, key="sai_qty")
        with col2:
            obs = st.text_input("Observação", key="sai_obs")

        if st.button("✅ Registrar Saída", type="primary"):
            novo_saldo = saldo_atual - int(qty)
            if novo_saldo < estoque_min:
                st.session_state["alerta_ruptura"] = f"Atenção: O saldo de '{nome_sel}' caiu para {novo_saldo} un., ficando abaixo do mínimo ({estoque_min})."
            
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo_saldo)
                registrar_movimentacao(conn, id_sel, "Saída", -int(qty), novo_saldo, obs)
                
            with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                sincronizar_tudo()
                
            st.toast("Saída registrada e dados exportados!", icon="📤")
            st.rerun()

# ═════════════════════════════════════════════════════════════
# AJUSTE
# ═════════════════════════════════════════════════════════════
with aba_ajuste:
    st.subheader("Ajuste de Estoque")
    produtos_df = listar_produtos()
    if produtos_df.empty:
        st.warning("⚠️ Nenhum produto cadastrado.")
    else:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="aju_prod")
        id_sel = opcoes[nome_sel]
        saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])
        estoque_min = int(produtos_df.loc[produtos_df["id"] == id_sel, "estoque_minimo"].values[0])

        col1, col2 = st.columns(2)
        with col1:
            novo_saldo = st.number_input("Novo saldo", min_value=0, step=1, value=saldo_atual, key="aju_qty")
        with col2:
            obs = st.text_input("Motivo", key="aju_obs")

        diferenca = int(novo_saldo) - saldo_atual
        if st.button("✅ Aplicar Ajuste", type="primary"):
            if novo_saldo < estoque_min:
                st.session_state["alerta_ruptura"] = f"Atenção: O ajuste deixou '{nome_sel}' abaixo do mínimo ({estoque_min})."

            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, int(novo_saldo))
                registrar_movimentacao(conn, id_sel, "Ajuste", diferenca, int(novo_saldo), obs)
                
            with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                sincronizar_tudo()
                
            st.toast("Ajuste realizado e dados exportados!", icon="🔧")
            st.rerun()

# ═════════════════════════════════════════════════════════════
# CONTAGEM
# ═════════════════════════════════════════════════════════════
with aba_contagem:
    st.subheader("Inventário / Contagem")
    produtos_df = listar_produtos()
    if produtos_df.empty:
        st.warning("⚠️ Nenhum produto cadastrado.")
    else:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="cnt_prod")
        id_sel = opcoes[nome_sel]
        saldo_sistemico = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])
        estoque_min = int(produtos_df.loc[produtos_df["id"] == id_sel, "estoque_minimo"].values[0])

        estoque_fisico = st.number_input("Estoque físico (contado)", min_value=0, step=1, key="cnt_qty")
        consumo = saldo_sistemico - int(estoque_fisico)
        divergencia_pct = (abs(consumo) / saldo_sistemico * 100) if saldo_sistemico > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Sistema", saldo_sistemico)
        c2.metric("Físico", estoque_fisico)
        c3.metric("Diferença", consumo)
        c4.metric("Divergência %", f"{divergencia_pct:.1f}%")

        if st.button("✅ Registrar Contagem", type="primary"):
            if estoque_fisico < estoque_min:
                st.session_state["alerta_ruptura"] = f"Atenção: A contagem revelou que '{nome_sel}' está abaixo do mínimo ({estoque_min})."

            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, estoque_fisico)
                registrar_movimentacao(conn, id_sel, "Contagem", -consumo, estoque_fisico, "Contagem semanal")
                
            with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                sincronizar_tudo()
                
            st.toast("Contagem registrada e dados exportados!", icon="📋")
            st.rerun()

# ═════════════════════════════════════════════════════════════
# HISTÓRICO E EXPORTAÇÃO
# ═════════════════════════════════════════════════════════════
with aba_historico:
    st.subheader("Histórico de Movimentações")
    movs_df = listar_movimentacoes()

    if movs_df.empty:
        st.info("Nenhuma movimentação registrada.")
    else:
        st.dataframe(
            movs_df.rename(columns={
                "id": "ID", "produto": "Produto", "data_hora": "Data/Hora",
                "tipo": "Tipo", "quantidade": "Quantidade", "saldo_resultante": "Saldo", "observacao": "Observação"
            }),
            width="stretch", hide_index=True
        )

        st.divider()
        col1, col2 = st.columns(2)
        
        with col1:
            csv_data = converter_para_csv(movs_df)
            st.download_button(
                label="📥 Baixar em CSV", data=csv_data, 
                file_name=f"movimentacoes_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv"
            )
            
        with col2:
            excel_data = converter_para_excel(movs_df)
            st.download_button(
                label="📥 Baixar em Excel (.xlsx)", data=excel_data, 
                file_name=f"movimentacoes_{datetime.now().strftime('%Y%m%d')}.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# ═════════════════════════════════════════════════════════════
# CADASTRAR / EXCLUIR PRODUTOS
# ═════════════════════════════════════════════════════════════
with aba_cadastro:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("➕ Novo Produto")
        nome_novo = st.text_input("Nome do produto")
        estoque_minimo = st.number_input("Estoque mínimo", min_value=0, value=10)
        valor_unitario = st.number_input("Valor unitário (R$)", min_value=0.0, step=0.01, format="%.2f")

        if st.button("✅ Cadastrar", type="primary"):
            if not nome_novo.strip():
                st.error("Informe um nome para o produto.")
            else:
                ok, msg = cadastrar_produto(nome_novo.strip(), estoque_minimo, valor_unitario)
                if ok:
                    with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                        sincronizar_tudo()
                    st.toast("Produto cadastrado com sucesso!", icon="➕")
                    st.rerun()
                else:
                    st.error(msg)

    with col2:
        st.subheader("🗑️ Excluir Produto")
        produtos_df = listar_produtos()
        if not produtos_df.empty:
            opcoes_del = dict(zip(produtos_df["nome"], produtos_df["id"]))
            nome_del = st.selectbox("Selecione um produto", list(opcoes_del.keys()), key="del_prod")
            st.warning("Ao excluir um produto, todo o histórico de movimentação dele será apagado.")
            if st.button("🗑️ Confirmar Exclusão"):
                deletar_produto(opcoes_del[nome_del])
                with st.spinner("Sincronizando banco e exportando CSVs para o BI..."):
                    sincronizar_tudo()
                st.toast("Produto excluído com sucesso!", icon="🗑️")
                st.rerun()
        else:
            st.info("Nenhum produto cadastrado.")