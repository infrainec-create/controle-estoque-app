import streamlit as st
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from io import BytesIO
import os
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA E CONSTANTES
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Controle de Estoque", page_icon="📦", layout="wide")
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
        if resultados.get('files', []):
            id_ficheiro = resultados['files'][0]['id']
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
        ficheiros = servico.files().list(q=query, fields="files(id)").execute().get('files', [])
        media = MediaFileUpload(DB_PATH, mimetype='application/x-sqlite3', resumable=True)
        if ficheiros:
            servico.files().update(fileId=ficheiros[0]['id'], media_body=media).execute()
        else:
            servico.files().create(body={'name': DB_PATH, 'parents': [FOLDER_ID]}, media_body=media).execute()
    except Exception as e:
        st.error(f"Erro ao enviar banco para o Drive: {e}")

def sincronizar_csv_drive(df, nome_arquivo):
    try:
        servico = obter_servico_drive()
        query = f"name='{nome_arquivo}' and '{FOLDER_ID}' in parents and trashed=false"
        ficheiros = servico.files().list(q=query, fields="files(id)").execute().get('files', [])
        csv_bytes = df.to_csv(index=False).encode('utf-8-sig')
        media = MediaIoBaseUpload(BytesIO(csv_bytes), mimetype='text/csv', resumable=True)
        if ficheiros:
            servico.files().update(fileId=ficheiros[0]['id'], media_body=media).execute()
        else:
            servico.files().create(body={'name': nome_arquivo, 'parents': [FOLDER_ID]}, media_body=media).execute()
    except Exception:
        pass

def sincronizar_tudo():
    enviar_para_o_drive()
    sincronizar_csv_drive(listar_produtos(), "produtos_looker.csv")
    sincronizar_csv_drive(listar_movimentacoes(), "movimentacoes_looker.csv")

# ─────────────────────────────────────────────────────────────
# EXPORTAÇÕES LOCAIS
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
                valor_unitario REAL DEFAULT 0,
                categoria TEXT DEFAULT 'Geral',
                lead_time INTEGER DEFAULT 3
            );
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto INTEGER NOT NULL REFERENCES produtos(id),
                data_hora TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('Entrada','Saída','Ajuste','Contagem')),
                quantidade INTEGER NOT NULL,
                saldo_resultante INTEGER NOT NULL,
                observacao TEXT
            );
        """)
        
        cursor = conn.execute("PRAGMA table_info(produtos)")
        colunas = [col[1] for col in cursor.fetchall()]
        if 'categoria' not in colunas:
            conn.execute("ALTER TABLE produtos ADD COLUMN categoria TEXT DEFAULT 'Geral'")
        if 'lead_time' not in colunas:
            conn.execute("ALTER TABLE produtos ADD COLUMN lead_time INTEGER DEFAULT 3")

        if conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 0:
            conn.executemany("""
                INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                ("Papel higiênico", 120, 50, 1.80, "Limpeza", 2),
                ("Sabonete líquido", 30, 10, 12.50, "Limpeza", 5),
                ("Saco de lixo", 18, 30, 0.90, "Limpeza", 3),
            ])

def listar_produtos():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)

def listar_movimentacoes():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
            FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
        """, conn)

def atualizar_saldo(conn, id_produto, novo_saldo):
    conn.execute("UPDATE produtos SET saldo_atual = ? WHERE id = ?", (novo_saldo, id_produto))

def registrar_movimentacao(conn, id_produto, tipo, quantidade, saldo_resultante, obs):
    data_hora = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
    conn.execute("""
        INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (id_produto, data_hora, tipo, quantidade, saldo_resultante, obs))

def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time)
                VALUES (?, 0, ?, ?, ?, ?)
            """, (nome, estoque_minimo, valor_unitario, categoria, lead_time))
        return True, "Cadastrado com sucesso."
    except sqlite3.IntegrityError:
        return False, "Produto já existe."

def editar_produto(id_produto, nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn:
            conn.execute("""
                UPDATE produtos 
                SET nome = ?, estoque_minimo = ?, valor_unitario = ?, categoria = ?, lead_time = ?
                WHERE id = ?
            """, (nome, estoque_minimo, valor_unitario, categoria, lead_time, id_produto))
        return True, "Produto atualizado com sucesso."
    except sqlite3.IntegrityError:
        return False, "Já existe outro produto com este nome."

def deletar_produto(id_produto):
    with get_conn() as conn:
        conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
        conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))

# ─────────────────────────────────────────────────────────────
# INICIALIZAÇÃO
# ─────────────────────────────────────────────────────────────
if "db_sincronizado" not in st.session_state:
    if not descarregar_do_drive(): sincronizar_tudo()
    init_db()
    st.session_state["db_sincronizado"] = True

if "alerta_ruptura" not in st.session_state:
    st.session_state["alerta_ruptura"] = None

# ─────────────────────────────────────────────────────────────
# INTERFACE
# ─────────────────────────────────────────────────────────────
st.title("📦 Controle de Estoque")
st.caption("Controle logístico de insumos com WMS e integração de BI")

if st.session_state["alerta_ruptura"]:
    st.warning(st.session_state["alerta_ruptura"], icon="🚨")
    st.session_state["alerta_ruptura"] = None

st.divider()

aba_ia, aba_painel, aba_entrada, aba_saida, aba_ajuste, aba_contagem, aba_historico, aba_cadastro = st.tabs([
    "🧠 Assistente IA", "📊 Painel", "⬇️ Entrada", "⬆️ Saída", "🔧 Ajuste", "📋 Contagem", "📜 Histórico", "⚙️ Produtos"
])

# IA
with aba_ia:
    st.subheader("🤖 Analista Logístico Virtual")
    if st.button("✨ Gerar Análise", type="primary"):
        produtos_df = listar_produtos()
        if not produtos_df.empty:
            with st.spinner("Analisando..."):
                try:
                    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                    modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    mod = next((m for m in modelos if 'flash' in m or 'pro' in m), modelos[0])
                    dados = produtos_df[["categoria", "nome", "saldo_atual", "estoque_minimo", "lead_time"]].to_string(index=False)
                    prompt = f"Analise o estoque logístico:\n{dados}\nResumo, alertas críticos e recomendação considerando o Lead Time."
                    st.write(genai.GenerativeModel(mod).generate_content(prompt).text)
                except Exception as e: st.error(f"Erro IA: {e}")

# PAINEL
with aba_painel:
    produtos_df = listar_produtos()
    movs_df = listar_movimentacoes()

    if not produtos_df.empty:
        produtos_df["valor_total"] = produtos_df["saldo_atual"] * produtos_df["valor_unitario"]
        
        # Curva ABC
        df_abc = produtos_df[produtos_df["valor_total"] > 0].copy()
        if not df_abc.empty:
            df_abc = df_abc.sort_values(by="valor_total", ascending=False)
            df_abc["perc_acumulado"] = (df_abc["valor_total"] / df_abc["valor_total"].sum()).cumsum()
            df_abc["Curva ABC"] = df_abc["perc_acumulado"].apply(lambda x: 'A' if x<=0.8 else ('B' if x<=0.95 else 'C'))
            produtos_df = produtos_df.merge(df_abc[["id", "Curva ABC"]], on="id", how="left").fillna({"Curva ABC": "-"})
        else: produtos_df["Curva ABC"] = "-"

        # Semáforo
        def status(row):
            if row['saldo_atual'] <= 0: return '🔴 Zerado'
            elif row['saldo_atual'] < row['estoque_minimo']: return '🔴 Crítico'
            elif row['saldo_atual'] <= (row['estoque_minimo'] * 1.3): return '🟡 Atenção'
            return '🟢 Saudável'
        produtos_df['Status'] = produtos_df.apply(status, axis=1)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Produtos", len(produtos_df))
        c2.metric("Total estoque", int(produtos_df["saldo_atual"].sum()))
        c3.metric("Movimentações", len(movs_df))
        c4.metric("Valor estoque", f"R$ {produtos_df['valor_total'].sum():,.2f}")

        st.divider()
        col1, col2 = st.columns(2, gap="large")

        with col1:
            st.markdown("### 📦 Posição")
            st.dataframe(produtos_df[["Status", "categoria", "nome", "saldo_atual", "estoque_minimo", "lead_time"]].rename(columns={"categoria": "Setor", "nome": "Produto", "saldo_atual": "Saldo", "estoque_minimo": "Mínimo"}), hide_index=True)

        with col2:
            st.markdown("### 🛒 Reposição (Cálculo WMS)")
            with get_conn() as conn:
                df_compra = pd.read_sql("""
                    SELECT p.categoria, p.nome AS Produto, p.saldo_atual, p.lead_time, p.estoque_minimo,
                           COALESCE(SUM(ABS(m.quantidade)), 0) AS consumo_total
                    FROM produtos p LEFT JOIN movimentacoes m ON p.id = m.id_produto AND m.tipo = 'Saída' GROUP BY p.id
                """, conn)

            df_compra["consumo_diario"] = df_compra["consumo_total"] / 30
            df_compra["Minimo Ideal"] = (df_compra["consumo_diario"] * df_compra["lead_time"] * 1.2).astype(int)
            df_compra["Alvo"] = df_compra[["estoque_minimo", "Minimo Ideal"]].max(axis=1)
            df_compra["Sugestão Compra"] = (df_compra["Alvo"] - df_compra["saldo_atual"]).clip(lower=0)

            st.dataframe(df_compra[["Produto", "lead_time", "Minimo Ideal", "saldo_atual", "Sugestão Compra"]].rename(columns={"lead_time": "Entrega(d)", "saldo_atual": "Saldo", "Sugestão Compra": "Comprar"}), hide_index=True)

# ENTRADA
with aba_entrada:
    produtos_df = listar_produtos()
    if not produtos_df.empty:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto (Entrada)", list(opcoes.keys()))
        qty = st.number_input("Qtd (Entrada)", min_value=1, step=1)
        obs = st.text_input("Obs (Entrada)")
        if st.button("✅ Registrar Entrada", type="primary"):
            id_sel = opcoes[nome_sel]
            novo = int(produtos_df.loc[produtos_df["id"]==id_sel, "saldo_atual"].values[0]) + int(qty)
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo)
                registrar_movimentacao(conn, id_sel, "Entrada", int(qty), novo, obs)
            with st.spinner("Sincronizando..."): sincronizar_tudo()
            st.rerun()

# SAÍDA
with aba_saida:
    if not produtos_df.empty:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto (Saída)", list(opcoes.keys()))
        id_sel = opcoes[nome_sel]
        saldo_atual = int(produtos_df.loc[produtos_df["id"]==id_sel, "saldo_atual"].values[0])
        minimo = int(produtos_df.loc[produtos_df["id"]==id_sel, "estoque_minimo"].values[0])
        qty = st.number_input("Qtd (Saída)", min_value=1, max_value=max(saldo_atual, 1), step=1)
        obs = st.text_input("Obs (Saída)")
        if st.button("✅ Registrar Saída", type="primary"):
            novo = saldo_atual - int(qty)
            if novo < minimo: st.session_state["alerta_ruptura"] = f"Atenção: '{nome_sel}' abaixo do mínimo."
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo)
                registrar_movimentacao(conn, id_sel, "Saída", -int(qty), novo, obs)
            with st.spinner("Sincronizando..."): sincronizar_tudo()
            st.rerun()

# AJUSTE
with aba_ajuste:
    if not produtos_df.empty:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto (Ajuste)", list(opcoes.keys()))
        id_sel = opcoes[nome_sel]
        saldo = int(produtos_df.loc[produtos_df["id"]==id_sel, "saldo_atual"].values[0])
        novo = st.number_input("Novo Saldo", min_value=0, value=saldo, step=1)
        obs = st.text_input("Motivo")
        if st.button("✅ Ajustar", type="primary"):
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, novo)
                registrar_movimentacao(conn, id_sel, "Ajuste", novo-saldo, novo, obs)
            with st.spinner("Sincronizando..."): sincronizar_tudo()
            st.rerun()

# CONTAGEM
with aba_contagem:
    if not produtos_df.empty:
        opcoes = dict(zip(produtos_df["nome"], produtos_df["id"]))
        nome_sel = st.selectbox("Produto (Contagem)", list(opcoes.keys()))
        id_sel = opcoes[nome_sel]
        saldo = int(produtos_df.loc[produtos_df["id"]==id_sel, "saldo_atual"].values[0])
        fisico = st.number_input("Físico", min_value=0, step=1)
        if st.button("✅ Registrar Contagem", type="primary"):
            with get_conn() as conn:
                atualizar_saldo(conn, id_sel, fisico)
                registrar_movimentacao(conn, id_sel, "Contagem", fisico-saldo, fisico, "Inventário")
            with st.spinner("Sincronizando..."): sincronizar_tudo()
            st.rerun()

# HISTÓRICO
with aba_historico:
    movs_df = listar_movimentacoes()
    if not movs_df.empty:
        st.dataframe(movs_df, hide_index=True)
        st.download_button("📥 CSV", converter_para_csv(movs_df), "movs.csv", "text/csv")

# GESTÃO DE PRODUTOS (CADASTRAR / EDITAR / EXCLUIR)
with aba_cadastro:
    aba_novo, aba_editar, aba_excluir = st.tabs(["➕ Novo Produto", "✏️ Editar Produto", "🗑️ Excluir Produto"])
    
    with aba_novo:
        cat = st.selectbox("Categoria", ["Limpeza", "Copa", "Escritório", "EPI", "Geral"])
        nome = st.text_input("Nome")
        minimo = st.number_input("Mínimo", min_value=0, value=10)
        lead = st.number_input("Lead Time (dias)", min_value=1, value=3)
        valor = st.number_input("Valor Un.", min_value=0.0, format="%.2f")
        if st.button("✅ Cadastrar Produto"):
            if nome.strip():
                ok, msg = cadastrar_produto(nome.strip(), minimo, valor, cat, lead)
                if ok:
                    with st.spinner("Sincronizando..."): sincronizar_tudo()
                    st.rerun()
                else: st.error(msg)
    
    with aba_editar:
        produtos_df = listar_produtos()
        if not produtos_df.empty:
            opcoes_edit = dict(zip(produtos_df["nome"], produtos_df["id"]))
            nome_edit = st.selectbox("Selecione o produto", list(opcoes_edit.keys()), key="sel_edit")
            id_edit = opcoes_edit[nome_edit]
            
            # Puxa os dados atuais do produto selecionado
            prod_atual = produtos_df[produtos_df["id"] == id_edit].iloc[0]
            
            lista_cats = ["Geral", "Limpeza", "Copa", "Escritório", "EPI", "Operação", "Outros"]
            if prod_atual["categoria"] not in lista_cats:
                lista_cats.append(prod_atual["categoria"])
                
            nova_cat = st.selectbox("Categoria", lista_cats, index=lista_cats.index(prod_atual["categoria"]), key="ed_cat")
            novo_nome = st.text_input("Nome", value=prod_atual["nome"], key="ed_nome")
            novo_min = st.number_input("Mínimo", min_value=0, value=int(prod_atual["estoque_minimo"]), key="ed_min")
            novo_lead = st.number_input("Lead Time (dias)", min_value=1, value=int(prod_atual["lead_time"]), key="ed_lead")
            novo_val = st.number_input("Valor Un.", min_value=0.0, value=float(prod_atual["valor_unitario"]), format="%.2f", key="ed_val")
            
            if st.button("✏️ Salvar Alterações", type="primary"):
                if novo_nome.strip():
                    ok, msg = editar_produto(id_edit, novo_nome.strip(), novo_min, novo_val, nova_cat, novo_lead)
                    if ok:
                        with st.spinner("Sincronizando..."): sincronizar_tudo()
                        st.rerun()
                    else: st.error(msg)
        else:
            st.info("Nenhum produto cadastrado.")

    with aba_excluir:
        if not produtos_df.empty:
            del_nome = st.selectbox("Produto a excluir", list(dict(zip(produtos_df["nome"], produtos_df["id"])).keys()))
            st.warning("Todo o histórico deste produto será apagado.")
            if st.button("🗑️ Confirmar Exclusão"):
                deletar_produto(dict(zip(produtos_df["nome"], produtos_df["id"]))[del_nome])
                with st.spinner("Sincronizando..."): sincronizar_tudo()
                st.rerun()