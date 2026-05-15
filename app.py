import streamlit as st
import sqlite3
from datetime import datetime
import pandas as pd

# ─── Configuração da página ───────────────────────────────────────────────────
st.set_page_config(
    page_title="Controle de Estoque",
    page_icon="📦",
    layout="wide",
)

DB_PATH = "estoque.db"

# ─── Funções de Exportação (NOVO) ─────────────────────────────────────────────
@st.cache_data
def converter_para_csv(df):
    # O utf-8-sig garante que o Excel/Power BI leiam os acentos corretamente
    return df.to_csv(index=False).encode('utf-8-sig')

# ─── Banco de dados ───────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS produtos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nome        TEXT    NOT NULL UNIQUE,
                saldo_atual INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS movimentacoes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto        INTEGER NOT NULL REFERENCES produtos(id),
                data_hora         TEXT    NOT NULL,
                tipo              TEXT    NOT NULL
                                  CHECK(tipo IN ('Entrada','Saída','Ajuste','Contagem')),
                quantidade        INTEGER NOT NULL,
                saldo_resultante  INTEGER NOT NULL,
                observacao        TEXT
            );
        """)
        # Produtos de exemplo (só insere se a tabela estiver vazia)
        cursor = conn.execute("SELECT COUNT(*) FROM produtos")
        if cursor.fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO produtos (nome, saldo_atual) VALUES (?, ?)",
                [
                    ("Caixa de papelão P", 120),
                    ("Fita adesiva (rolo)", 45),
                    ("Lacre plástico (cx 100)", 8),
                    ("Filme stretch (kg)", 32),
                ],
            )

def listar_produtos():
    with get_conn() as conn:
        return pd.read_sql("SELECT id, nome, saldo_atual FROM produtos ORDER BY nome", conn)

def listar_movimentacoes():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                m.id,
                p.nome          AS produto,
                m.data_hora,
                m.tipo,
                m.quantidade,
                m.saldo_resultante,
                m.observacao
            FROM movimentacoes m
            JOIN produtos p ON p.id = m.id_produto
            ORDER BY m.id DESC
        """, conn)

def atualizar_saldo(conn, id_produto, novo_saldo):
    conn.execute(
        "UPDATE produtos SET saldo_atual = ? WHERE id = ?",
        (novo_saldo, id_produto),
    )

def registrar_movimentacao(conn, id_produto, tipo, quantidade, saldo_resultante, obs):
    conn.execute(
        """INSERT INTO movimentacoes
           (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            id_produto,
            datetime.now().strftime("%d/%m/%Y %H:%M"),
            tipo,
            quantidade,
            saldo_resultante,
            obs,
        ),
    )

def cadastrar_produto(nome: str):
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO produtos (nome, saldo_atual) VALUES (?, 0)", (nome,))
        return True, f'Produto "{nome}" cadastrado com saldo zero.'
    except sqlite3.IntegrityError:
        return False, f'Já existe um produto com o nome "{nome}".'
def deletar_produto(id_produto):
    with get_conn() as conn:
        # 1. Apaga primeiro as movimentações para manter a integridade do banco
        conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
        # 2. Apaga o produto do sistema
        conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))

# ─── Inicialização ────────────────────────────────────────────────────────────
init_db()

# ─── Cabeçalho ────────────────────────────────────────────────────────────────
st.title("📦 Controle de Estoque")
st.caption("Entradas · Saídas · Ajustes · Contagem Semanal")
st.divider()

# ─── Abas ─────────────────────────────────────────────────────────────────────
aba_painel, aba_entrada, aba_saida, aba_ajuste, aba_contagem, aba_historico, aba_cadastro = st.tabs([
    "📊 Painel",
    "⬇️ Entrada",
    "⬆️ Saída",
    "🔧 Ajuste",
    "📋 Contagem Semanal",
    "📜 Histórico",
    "➕ Cadastrar Produto",
])

# ══════════════════════════════════════════════════════════════════════════════
# PAINEL
# ══════════════════════════════════════════════════════════════════════════════
with aba_painel:
    produtos_df = listar_produtos()
    movs_df = listar_movimentacoes()

    total_itens = len(produtos_df)
    total_saldo = int(produtos_df["saldo_atual"].sum())
    saldo_baixo = int((produtos_df["saldo_atual"] < 10).sum())
    total_movs = len(movs_df)

    # Métricas principais
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Produtos cadastrados", total_itens)
    c2.metric("Total em estoque", total_saldo)
    c3.metric("Saldo baixo (< 10)", saldo_baixo, delta=None)
    c4.metric("Movimentações", total_movs)

    st.divider()

    # Colunas para dividir a tela: Produtos à esquerda, Compras à direita
    col_esq, col_dir = st.columns(2)

    with col_esq:
        st.subheader("📦 Posição de Estoque")
        st.dataframe(
            produtos_df.rename(columns={"id": "ID", "nome": "Produto", "saldo_atual": "Saldo atual"}),
            use_container_width=True,
            hide_index=True,
        )
        if saldo_baixo > 0:
            st.warning(f"⚠️ {saldo_baixo} produto(s) com saldo crítico (abaixo de 10 unidades).")

    with col_dir:
        st.subheader("🛒 Análise de Consumo e Compras")
        
        # Consulta SQL que soma as Saídas e Contagens (onde a quantidade foi negativa)
        with get_conn() as conn:
            df_compras = pd.read_sql("""
                SELECT 
                    p.nome AS Produto,
                    p.saldo_atual AS [Saldo atual],
                    COALESCE(SUM(ABS(m.quantidade)), 0) AS Consumo
                FROM produtos p
                LEFT JOIN movimentacoes m 
                    ON p.id = m.id_produto 
                    AND m.tipo IN ('Saída', 'Contagem') 
                    AND m.quantidade < 0
                GROUP BY p.id, p.nome, p.saldo_atual
            """, conn)
            
        # Lógica de reposição: Consumo registrado - Saldo Atual
        df_compras["Sugestão de Compra"] = (df_compras["Consumo"] - df_compras["Saldo atual"]).clip(lower=0)
        
        # Reordenando as colunas
        df_compras = df_compras[["Produto", "Consumo", "Saldo atual", "Sugestão de Compra"]]
        
        st.dataframe(
            df_compras,
            use_container_width=True,
            hide_index=True,
        )
        st.caption("A sugestão de compra indica a quantidade necessária para cobrir o consumo histórico registrado no sistema.")

# ══════════════════════════════════════════════════════════════════════════════
# ENTRADA
# ══════════════════════════════════════════════════════════════════════════════
with aba_entrada:
    st.subheader("Registrar entrada de mercadoria")
    produtos_df = listar_produtos()

    opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
    nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="ent_prod")
    id_sel = opcoes[nome_sel]
    saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])

    col1, col2 = st.columns(2)
    with col1:
        qty = st.number_input("Quantidade a entrar", min_value=1, step=1, key="ent_qty")
    with col2:
        obs = st.text_input("Observação (opcional)", placeholder="Ex: Compra fornecedor XYZ", key="ent_obs")

    st.info(f"Saldo atual: **{saldo_atual}** → Novo saldo após entrada: **{saldo_atual + int(qty)}**")

    if st.button("✅ Confirmar Entrada", type="primary", key="btn_entrada"):
        novo_saldo = saldo_atual + int(qty)
        with get_conn() as conn:
            atualizar_saldo(conn, id_sel, novo_saldo)
            registrar_movimentacao(conn, id_sel, "Entrada", int(qty), novo_saldo, obs)
        st.success(f"Entrada de {int(qty)} unidades registrada. Novo saldo: {novo_saldo}")
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# SAÍDA
# ══════════════════════════════════════════════════════════════════════════════
with aba_saida:
    st.subheader("Registrar saída de mercadoria")
    produtos_df = listar_produtos()

    opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
    nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="sai_prod")
    id_sel = opcoes[nome_sel]
    saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])

    col1, col2 = st.columns(2)
    with col1:
        qty = st.number_input("Quantidade a sair", min_value=1, max_value=max(saldo_atual, 1), step=1, key="sai_qty")
    with col2:
        obs = st.text_input("Observação (opcional)", placeholder="Ex: Envio filial Sul", key="sai_obs")

    novo_saldo_prev = saldo_atual - int(qty)
    if novo_saldo_prev < 0:
        st.error("Quantidade maior que o saldo disponível.")
    else:
        st.info(f"Saldo atual: **{saldo_atual}** → Novo saldo após saída: **{novo_saldo_prev}**")

    if st.button("✅ Confirmar Saída", type="primary", key="btn_saida"):
        if int(qty) > saldo_atual:
            st.error("Quantidade inválida: maior que o saldo disponível.")
        else:
            novo_saldo = saldo_atual - int(qty)
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo_saldo)
                registrar_movimentacao(conn, id_sel, "Saída", -int(qty), novo_saldo, obs)
            st.success(f"Saída de {int(qty)} unidades registrada. Novo saldo: {novo_saldo}")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# AJUSTE
# ══════════════════════════════════════════════════════════════════════════════
with aba_ajuste:
    st.subheader("Ajuste de saldo")
    st.caption("Use para corrigir divergências por avaria, perda ou auditoria. O saldo será substituído pelo novo valor informado.")
    produtos_df = listar_produtos()

    opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
    nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="adj_prod")
    id_sel = opcoes[nome_sel]
    saldo_atual = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])

    col1, col2 = st.columns(2)
    with col1:
        novo_saldo = st.number_input("Novo saldo (quantidade física real)", min_value=0, step=1,
                                     value=saldo_atual, key="adj_qty")
    with col2:
        obs = st.text_input("Motivo do ajuste", placeholder="Ex: Avaria detectada", key="adj_obs")

    diferenca = int(novo_saldo) - saldo_atual
    sinal = "+" if diferenca >= 0 else ""
    st.info(
        f"Saldo sistêmico: **{saldo_atual}** → "
        f"Diferença: **{sinal}{diferenca}** → "
        f"Novo saldo: **{int(novo_saldo)}**"
    )

    if st.button("✅ Aplicar Ajuste", type="primary", key="btn_ajuste"):
        with get_conn() as conn:
            atualizar_saldo(conn, id_sel, int(novo_saldo))
            registrar_movimentacao(conn, id_sel, "Ajuste", diferenca, int(novo_saldo), obs)
        st.success(f"Saldo ajustado para {int(novo_saldo)} unidades.")
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# CONTAGEM SEMANAL
# ══════════════════════════════════════════════════════════════════════════════
with aba_contagem:
    st.subheader("📋 Contagem Semanal — Inventário")
    st.caption(
        "Informe o estoque físico que você contou na prateleira. "
        "O sistema calcula o consumo do período automaticamente."
    )
    produtos_df = listar_produtos()

    opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
    nome_sel = st.selectbox("Produto", list(opcoes.keys()), key="cnt_prod")
    id_sel = opcoes[nome_sel]
    saldo_sistemico = int(produtos_df.loc[produtos_df["id"] == id_sel, "saldo_atual"].values[0])

    estoque_fisico = st.number_input(
        "Estoque físico contado (o que você contou com as mãos)",
        min_value=0, step=1, key="cnt_qty"
    )
    estoque_fisico = int(estoque_fisico)

    # ── Cálculo do consumo ──────────────────────────────────────────────────
    consumo = saldo_sistemico - estoque_fisico

    col1, col2, col3 = st.columns(3)
    col1.metric("Estoque sistêmico", saldo_sistemico)
    col2.metric("Estoque físico contado", estoque_fisico)
    col3.metric(
        "Consumo calculado",
        consumo if consumo >= 0 else f"+{abs(consumo)} (ganho)",
        delta=None,
    )

    if consumo > 0:
        st.warning(
            f"**Consumo no período: {consumo} unidades.**\n\n"
            f"Uma movimentação de saída com tag 'Contagem' será registrada, "
            f"e o saldo passará de {saldo_sistemico} → {estoque_fisico}."
        )
    elif consumo < 0:
        st.info(
            f"O estoque físico é **maior** que o sistêmico ({abs(consumo)} unidades a mais). "
            f"O saldo será ajustado para cima."
        )
    else:
        st.success("Estoque físico bate com o sistêmico. Nenhuma divergência!")

    if st.button("✅ Registrar Contagem", type="primary", key="btn_contagem"):
        with get_conn() as conn:
            atualizar_saldo(conn, id_sel, estoque_fisico)
            registrar_movimentacao(
                conn, id_sel, "Contagem", -consumo, estoque_fisico,
                f"Contagem semanal — consumo de {consumo} unidades"
            )
        st.success(
            f"Contagem registrada! Consumo do período: {consumo} unidades. "
            f"Novo saldo: {estoque_fisico}"
        )
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# HISTÓRICO
# ══════════════════════════════════════════════════════════════════════════════
with aba_historico:
    st.subheader("Histórico de movimentações")
    movs_df = listar_movimentacoes()

    if movs_df.empty:
        st.info("Nenhuma movimentação registrada ainda.")
    else:
        # Filtro por tipo
        tipos = ["Todos"] + list(movs_df["tipo"].unique())
        filtro = st.selectbox("Filtrar por tipo", tipos, key="hist_filtro")
        
        df_filtrado = movs_df.copy()
        if filtro != "Todos":
            df_filtrado = movs_df[movs_df["tipo"] == filtro]

        st.dataframe(
            df_filtrado.rename(columns={
                "id": "ID", "produto": "Produto", "data_hora": "Data/Hora",
                "tipo": "Tipo", "quantidade": "Quantidade",
                "saldo_resultante": "Saldo resultante", "observacao": "Observação"
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"{len(df_filtrado)} registro(s) exibido(s).")

        # ── NOVO: Botão de Download para BI ─────────────────────────────────
        st.divider()
        csv_data = converter_para_csv(df_filtrado)
        
        st.download_button(
            label="📥 Baixar Dados Filtrados (CSV)",
            data=csv_data,
            file_name=f"movimentacoes_estoque_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            help="Faça o download do histórico atual para subir no SharePoint e conectar ao Power BI."
        )

# ══════════════════════════════════════════════════════════════════════════════
# CADASTRAR PRODUTO
# ══════════════════════════════════════════════════════════════════════════════
with aba_cadastro:
    col_cad, col_del = st.columns(2)
    
    # Coluna Esquerda: Cadastro
    with col_cad:
        st.subheader("➕ Cadastrar novo produto")
        nome_novo = st.text_input("Nome do produto", placeholder="Ex: Palete PBR")

        if st.button("✅ Cadastrar", type="primary", key="btn_cadastro"):
            if not nome_novo.strip():
                st.error("Informe um nome para o produto.")
            else:
                ok, msg = cadastrar_produto(nome_novo.strip())
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
                    
    # Coluna Direita: Exclusão
    with col_del:
        st.subheader("🗑️ Excluir produto")
        produtos_df = listar_produtos()
        
        if not produtos_df.empty:
            opcoes_del = dict(zip(produtos_df["nome"], produtos_df["id"]))
            nome_del = st.selectbox("Selecione para excluir", list(opcoes_del.keys()), key="del_prod")
            
            st.warning("⚠️ Aviso: Apagar o produto também apagará permanentemente todo o seu histórico no sistema.")
            
            if st.button("🗑️ Confirmar Exclusão", type="secondary"):
                deletar_produto(opcoes_del[nome_del])
                st.rerun()
        else:
            st.info("Nenhum produto para excluir.")

    # Tabela embaixo
    st.divider()
    st.subheader("📦 Produtos cadastrados")
    if not produtos_df.empty:
        st.dataframe(
            produtos_df.rename(columns={"id": "ID", "nome": "Produto", "saldo_atual": "Saldo atual"}),
            use_container_width=True,
            hide_index=True,
        )