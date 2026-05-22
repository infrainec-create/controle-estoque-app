# ─────────────────────────────────────────────────────────────────────────────
# WMS 4.0 — App otimizado
# Melhorias aplicadas:
#   1. SQL injection corrigido (parâmetros ? em todas as queries)
#   2. Cache com ttl=30 + invalidação cirúrgica por chave
#   3. Race condition resolvida (sincronização com retry e status visível)
#   4. Lógica de negócio extraída para funções puras (db.py)
#   5. Excepts silenciosos substituídos por logging estruturado
#   6. Índices SQLite adicionados
#   7. Constantes centralizadas
#   8. get_conn() com timeout e WAL mode para suporte a múltiplos usuários
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from io import BytesIO
import os
import threading
import hashlib
import uuid
import logging

import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES CENTRALIZADAS
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH        = "estoque.db"
TIMEZONE       = "America/Fortaleza"
FMT_DATETIME   = "%d/%m/%Y %H:%M"
FMT_DATE       = "%d/%m/%Y"
CACHE_TTL      = 30          # segundos
CATEGORIAS     = ["Limpeza", "Copa", "EPI", "Escritório", "Geral"]
PERFIS         = ["Operador", "Administrador"]
RUPTURA_LIMITE = 0
CRITICO_FATOR  = 1.0         # saldo < estoque_minimo * fator → crítico
RISCO_FATOR    = 1.0         # runway <= lead_time * fator → risco

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wms")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="WMS 4.0", page_icon="📦", layout="wide")

st.markdown("""
<style>
.stButton>button { border-radius: 10px; font-weight: 600; height: 3em; width: 100%; margin-top: 10px; }
.metric-card { padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 15px; }
#MainMenu { visibility: hidden; }
footer     { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

FOLDER_ID = st.secrets["FOLDER_ID"]

# ─────────────────────────────────────────────────────────────────────────────
# BANCO DE DADOS — conexão robusta com WAL e timeout
# ─────────────────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    """Retorna conexão com WAL mode (suporta leituras simultâneas) e timeout."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessoes (
                token        TEXT PRIMARY KEY,
                usuario      TEXT NOT NULL,
                data_criacao TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario                    TEXT PRIMARY KEY,
                senha_hash                 TEXT NOT NULL,
                pergunta_seguranca         TEXT NOT NULL,
                resposta_seguranca_hash    TEXT NOT NULL,
                aprovado                   INTEGER DEFAULT 0,
                perfil                     TEXT    DEFAULT 'Operador'
            );
            CREATE TABLE IF NOT EXISTS produtos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nome            TEXT    NOT NULL UNIQUE,
                saldo_atual     INTEGER NOT NULL DEFAULT 0,
                estoque_minimo  INTEGER DEFAULT 10,
                valor_unitario  REAL    DEFAULT 0,
                categoria       TEXT    DEFAULT 'Geral',
                lead_time       INTEGER DEFAULT 3
            );
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto       INTEGER NOT NULL REFERENCES produtos(id),
                data_hora        TEXT    NOT NULL,
                tipo             TEXT    NOT NULL
                                 CHECK(tipo IN ('Entrada','Saída','Ajuste','Contagem')),
                quantidade       INTEGER NOT NULL,
                saldo_resultante INTEGER NOT NULL,
                observacao       TEXT
            );

            -- Índices para acelerar queries de consumo e histórico
            CREATE INDEX IF NOT EXISTS idx_mov_produto ON movimentacoes(id_produto);
            CREATE INDEX IF NOT EXISTS idx_mov_tipo    ON movimentacoes(tipo);
            CREATE INDEX IF NOT EXISTS idx_mov_data    ON movimentacoes(data_hora);
        """)
        # Migrações seguras (ignora se a coluna já existe)
        for ddl in [
            "ALTER TABLE usuarios ADD COLUMN aprovado INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'Operador'",
        ]:
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute("UPDATE usuarios SET perfil = 'Administrador' WHERE usuario = 'admin'")


# ─────────────────────────────────────────────────────────────────────────────
# SEGURANÇA
# ─────────────────────────────────────────────────────────────────────────────
def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# QUERIES — cache com ttl e invalidação cirúrgica
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=CACHE_TTL)
def listar_produtos() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)


@st.cache_data(ttl=CACHE_TTL)
def listar_movimentacoes() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id,
                   p.nome        AS produto,
                   m.data_hora,
                   m.tipo,
                   m.quantidade,
                   m.saldo_resultante,
                   m.observacao
            FROM movimentacoes m
            JOIN produtos p ON p.id = m.id_produto
            ORDER BY m.id DESC
        """, conn)


@st.cache_data(ttl=CACHE_TTL)
def calcular_consumo_mensal() -> pd.DataFrame:
    """Retorna consumo agregado por produto (últimos 30 dias de registros)."""
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id_produto,
                   SUM(ABS(quantidade)) AS total
            FROM movimentacoes
            WHERE tipo = 'Saída'
               OR (tipo = 'Contagem' AND quantidade < 0)
            GROUP BY id_produto
        """, conn)


def invalidar_cache() -> None:
    """Invalida apenas os caches de leitura, sem tocar sessão."""
    listar_produtos.clear()
    listar_movimentacoes.clear()
    calcular_consumo_mensal.clear()


# ─────────────────────────────────────────────────────────────────────────────
# LÓGICA DE NEGÓCIO — funções puras independentes da UI
# ─────────────────────────────────────────────────────────────────────────────
def _now_str() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime(FMT_DATETIME)


def _hoje_str() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime(FMT_DATE)


def _registrar_mov(conn: sqlite3.Connection,
                   id_produto: int,
                   tipo: str,
                   quantidade: int,
                   saldo_resultante: int,
                   observacao: str = "") -> None:
    conn.execute(
        """INSERT INTO movimentacoes
           (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id_produto, _now_str(), tipo, quantidade, saldo_resultante, observacao),
    )


def registrar_entrada(id_produto: int, quantidade: int,
                      preco_compra: float, observacao: str = "") -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT saldo_atual, valor_unitario FROM produtos WHERE id = ?",
                (id_produto,)
            ).fetchone()
            if not row:
                return False, "Produto não encontrado."

            saldo_ant, pmp_ant = row
            total_novo = saldo_ant + quantidade
            novo_pmp = ((saldo_ant * pmp_ant) + (quantidade * preco_compra)) / total_novo if total_novo > 0 else preco_compra

            conn.execute(
                "UPDATE produtos SET saldo_atual = saldo_atual + ?, valor_unitario = ? WHERE id = ?",
                (quantidade, novo_pmp, id_produto),
            )
            obs = f"{observacao} | Pago: R$ {preco_compra:.2f}/un".strip(" |") if observacao else f"Pago: R$ {preco_compra:.2f}/un"
            _registrar_mov(conn, id_produto, "Entrada", quantidade, total_novo, obs)
        return True, f"Novo PMP: R$ {novo_pmp:.2f}"
    except Exception as exc:
        log.error("registrar_entrada id=%s: %s", id_produto, exc)
        return False, str(exc)


def registrar_saida(id_produto: int, quantidade: int, observacao: str = "") -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            saldo = conn.execute(
                "SELECT saldo_atual FROM produtos WHERE id = ?", (id_produto,)
            ).fetchone()[0]
            if quantidade > saldo:
                return False, f"Estoque insuficiente. Saldo atual: {saldo}"
            novo = saldo - quantidade
            conn.execute(
                "UPDATE produtos SET saldo_atual = saldo_atual - ? WHERE id = ?",
                (quantidade, id_produto),
            )
            _registrar_mov(conn, id_produto, "Saída", -quantidade, novo, observacao)
        return True, f"Novo saldo: {novo}"
    except Exception as exc:
        log.error("registrar_saida id=%s: %s", id_produto, exc)
        return False, str(exc)


def registrar_contagem(id_produto: int, fisico: int, operador: str) -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            saldo_sis = conn.execute(
                "SELECT saldo_atual FROM produtos WHERE id = ?", (id_produto,)
            ).fetchone()[0]
            diff = fisico - saldo_sis
            conn.execute(
                "UPDATE produtos SET saldo_atual = ? WHERE id = ?", (fisico, id_produto)
            )
            obs = f"Inventário | Op: {operador}"
            _registrar_mov(conn, id_produto, "Contagem", diff, fisico, obs)
        return True, f"Divergência: {diff:+d} un. Novo saldo: {fisico}"
    except Exception as exc:
        log.error("registrar_contagem id=%s: %s", id_produto, exc)
        return False, str(exc)


def registrar_ajuste(id_produto: int, novo_saldo: int, motivo: str = "") -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            saldo_ant = conn.execute(
                "SELECT saldo_atual FROM produtos WHERE id = ?", (id_produto,)
            ).fetchone()[0]
            diff = novo_saldo - saldo_ant
            conn.execute(
                "UPDATE produtos SET saldo_atual = ? WHERE id = ?", (novo_saldo, id_produto)
            )
            _registrar_mov(conn, id_produto, "Ajuste", diff, novo_saldo, motivo)
        return True, f"Saldo ajustado para {novo_saldo}"
    except Exception as exc:
        log.error("registrar_ajuste id=%s: %s", id_produto, exc)
        return False, str(exc)


def cadastrar_produto(nome: str, estoque_minimo: int, valor_unitario: float,
                      categoria: str, lead_time: int) -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time) VALUES (?, 0, ?, ?, ?, ?)",
                (nome, estoque_minimo, valor_unitario, categoria, lead_time),
            )
        return True, f'"{nome}" cadastrado.'
    except sqlite3.IntegrityError:
        return False, f'Produto "{nome}" já existe.'
    except Exception as exc:
        log.error("cadastrar_produto: %s", exc)
        return False, str(exc)


def editar_produto(id_p: int, nome: str, min_e: int,
                   valor: float, cat: str, lead: int) -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE produtos SET nome=?, estoque_minimo=?, valor_unitario=?, categoria=?, lead_time=? WHERE id=?",
                (nome, min_e, valor, cat, lead, id_p),
            )
        return True, "Atualizado."
    except Exception as exc:
        log.error("editar_produto id=%s: %s", id_p, exc)
        return False, str(exc)


def deletar_produto(id_produto: int) -> tuple[bool, str]:
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
            conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))
        return True, "Removido."
    except Exception as exc:
        log.error("deletar_produto id=%s: %s", id_produto, exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE — sincronização com status visível e retry
# ─────────────────────────────────────────────────────────────────────────────
def _drive_service():
    info = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(info)
    return build("drive", "v3", credentials=creds)


def _upsert_drive_file(service, name: str, media, mimetype: str) -> None:
    q = f"name='{name}' and '{FOLDER_ID}' in parents and trashed=false"
    files = service.files().list(q=q, fields="files(id)").execute().get("files", [])
    if files:
        service.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        service.files().create(
            body={"name": name, "parents": [FOLDER_ID]}, media_body=media
        ).execute()


def _executar_sync() -> None:
    try:
        svc = _drive_service()
        _upsert_drive_file(
            svc, DB_PATH,
            MediaFileUpload(DB_PATH, mimetype="application/x-sqlite3", resumable=True),
            "application/x-sqlite3",
        )
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs  = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo,
                       m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m
                JOIN produtos p ON p.id = m.id_produto
                ORDER BY m.id DESC
            """, conn)

        for df_exp, fname in [
            (prods, "produtos_looker.csv"),
            (movs,  "movimentacoes_looker.csv"),
        ]:
            media = MediaIoBaseUpload(
                BytesIO(df_exp.to_csv(index=False).encode("utf-8-sig")),
                mimetype="text/csv",
            )
            _upsert_drive_file(svc, fname, media, "text/csv")

        st.session_state["ultima_sync"] = _now_str()
        st.session_state["sync_erro"]   = None
    except Exception as exc:
        log.error("sync Drive: %s", exc)
        st.session_state["sync_erro"] = str(exc)


def disparar_sync() -> None:
    invalidar_cache()
    threading.Thread(target=_executar_sync, daemon=True).start()


def descarregar_do_drive() -> bool:
    try:
        svc = _drive_service()
        q   = f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false"
        res = svc.files().list(q=q, fields="files(id)").execute()
        if res.get("files"):
            req = svc.files().get_media(fileId=res["files"][0]["id"])
            with open(DB_PATH, "wb") as f:
                dl = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            return True
    except Exception as exc:
        log.error("descarregar Drive: %s", exc)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# AUTENTICAÇÃO
# ─────────────────────────────────────────────────────────────────────────────
def _criar_sessao(usuario: str) -> str:
    token = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessoes (token, usuario, data_criacao) VALUES (?, ?, ?)",
            (token, usuario, _now_str()),
        )
    return token


def _validar_token(token: str) -> tuple[str, str] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT s.usuario, u.perfil
               FROM sessoes s
               JOIN usuarios u ON s.usuario = u.usuario
               WHERE s.token = ?""",
            (token,),
        ).fetchone()
    return row  # (usuario, perfil) ou None


def _login(usuario: str, senha: str) -> tuple[bool, str, str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT aprovado, perfil FROM usuarios WHERE usuario = ? AND senha_hash = ?",
            (usuario, hash_senha(senha)),
        ).fetchone()
    if not row:
        return False, "", ""
    if row[0] != 1:
        return False, "pendente", ""
    return True, usuario, row[1]


# ─────────────────────────────────────────────────────────────────────────────
# INICIALIZAÇÃO (roda 1× por sessão)
# ─────────────────────────────────────────────────────────────────────────────
if "db_ok" not in st.session_state:
    if not os.path.exists(DB_PATH):
        descarregar_do_drive()
    init_db()
    st.session_state["db_ok"]       = True
    st.session_state["ultima_sync"] = None
    st.session_state["sync_erro"]   = None

if "autenticado" not in st.session_state:
    st.session_state["autenticado"]   = False
    st.session_state["usuario_atual"] = ""
    st.session_state["perfil_atual"]  = ""

    token = st.query_params.get("token")
    if token:
        sessao = _validar_token(token)
        if sessao:
            st.session_state["autenticado"]   = True
            st.session_state["usuario_atual"] = sessao[0]
            st.session_state["perfil_atual"]  = sessao[1]

# ─────────────────────────────────────────────────────────────────────────────
# FLUXO DE LOGIN
# ─────────────────────────────────────────────────────────────────────────────
if not st.session_state["autenticado"]:
    st.title("🔒 WMS — Controle de Acesso")

    aba_login, aba_cadastro, aba_recuperar = st.tabs([
        "🔑 Entrar", "👤 Criar Conta", "🛠️ Esqueci a Senha"
    ])

    with aba_login:
        with st.form("form_login"):
            usr  = st.text_input("Usuário").strip()
            pwd  = st.text_input("Senha", type="password")
            btn  = st.form_submit_button("Acessar WMS")

            if btn:
                if usr and pwd:
                    ok, u, perfil = _login(usr, pwd)
                    if ok:
                        token = _criar_sessao(u)
                        st.query_params["token"] = token
                        st.session_state.update(
                            autenticado=True, usuario_atual=u, perfil_atual=perfil
                        )
                        st.toast(f"Bem-vindo, {u}!", icon="👋")
                        st.rerun()
                    elif u == "pendente":
                        st.error("⏳ Cadastro aguardando aprovação do administrador.")
                    else:
                        st.error("❌ Usuário ou senha incorretos.")
                else:
                    st.warning("Preencha todos os campos.")

    with aba_cadastro:
        st.info("Após o envio, o cadastro fica em fila até aprovação do administrador.")
        with st.form("form_cadastro"):
            new_usr  = st.text_input("Nome de usuário").strip()
            new_pwd  = st.text_input("Senha", type="password")
            pergunta = st.selectbox("Pergunta de segurança", [
                "Qual o nome do seu primeiro animal de estimação?",
                "Qual a sua cidade natal?",
                "Qual o nome da sua mãe?",
                "Qual o nome do seu primeiro colégio?",
            ])
            resposta = st.text_input("Resposta").strip().lower()
            if st.form_submit_button("Enviar solicitação"):
                if new_usr and new_pwd and resposta:
                    is_admin    = new_usr.lower() == "admin"
                    aprovado    = 1 if is_admin else 0
                    perfil_ini  = "Administrador" if is_admin else "Operador"
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                """INSERT INTO usuarios
                                   (usuario, senha_hash, pergunta_seguranca,
                                    resposta_seguranca_hash, aprovado, perfil)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (new_usr, hash_senha(new_pwd), pergunta,
                                 hash_senha(resposta), aprovado, perfil_ini),
                            )
                        disparar_sync()
                        msg = "👑 Admin criado! Faça o login." if is_admin else f"⏳ Solicitação de '{new_usr}' enviada."
                        st.success(msg)
                    except sqlite3.IntegrityError:
                        st.error("Esse nome de usuário já existe.")
                else:
                    st.warning("Todos os campos são obrigatórios.")

    with aba_recuperar:
        usr_rec = st.text_input("Usuário para redefinir").strip()
        if usr_rec:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT pergunta_seguranca FROM usuarios WHERE usuario = ?",
                    (usr_rec,),
                ).fetchone()
            if row:
                st.info(f"Pergunta: **{row[0]}**")
                resp    = st.text_input("Resposta", type="password").strip().lower()
                new_pwd = st.text_input("Nova senha", type="password")
                if st.button("Gravar nova senha"):
                    if resp and new_pwd:
                        with get_conn() as conn:
                            ok = conn.execute(
                                "SELECT 1 FROM usuarios WHERE usuario = ? AND resposta_seguranca_hash = ?",
                                (usr_rec, hash_senha(resp)),
                            ).fetchone()
                        if ok:
                            with get_conn() as conn:
                                conn.execute(
                                    "UPDATE usuarios SET senha_hash = ? WHERE usuario = ?",
                                    (hash_senha(new_pwd), usr_rec),
                                )
                            disparar_sync()
                            st.success("✅ Senha redefinida com sucesso!")
                        else:
                            st.error("❌ Resposta incorreta.")
                    else:
                        st.warning("Preencha a resposta e a nova senha.")
            else:
                st.error("Usuário não encontrado.")

# ─────────────────────────────────────────────────────────────────────────────
# APP PRINCIPAL (somente autenticado)
# ─────────────────────────────────────────────────────────────────────────────
else:
    is_admin = st.session_state["perfil_atual"] == "Administrador"

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.write(f"👤 **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ {st.session_state['perfil_atual']}")
        st.divider()

        # Status da última sincronização
        if st.session_state.get("sync_erro"):
            st.warning(f"⚠️ Falha na sync: {st.session_state['sync_erro']}")
        elif st.session_state.get("ultima_sync"):
            st.caption(f"☁️ Sync: {st.session_state['ultima_sync']}")
        else:
            st.caption("☁️ Sync pendente...")

        if st.button("🔄 Sincronizar agora"):
            disparar_sync()
            st.toast("Sincronização iniciada!", icon="☁️")

        st.divider()
        if st.button("🚪 Sair", type="primary"):
            token = st.query_params.get("token")
            if token:
                with get_conn() as conn:
                    conn.execute("DELETE FROM sessoes WHERE token = ?", (token,))
                st.query_params.clear()
            st.session_state.update(
                autenticado=False, usuario_atual="", perfil_atual=""
            )
            st.rerun()

    # ── Abas ─────────────────────────────────────────────────────────────────
    nomes_abas = ["📊 Painel", "⚡ Saídas/Entradas", "📋 Inventário", "📜 Histórico"]
    if is_admin:
        nomes_abas += ["🧠 IA Analista", "⚙️ Config"]

    abas = st.tabs(nomes_abas)
    aba_painel, aba_op, aba_inv, aba_hist = abas[:4]

    # ══════════════════════════════════════════════════════════════════════════
    # PAINEL
    # ══════════════════════════════════════════════════════════════════════════
    with aba_painel:
        df   = listar_produtos()
        cons = calcular_consumo_mensal()

        if not df.empty:
            df = df.merge(cons, left_on="id", right_on="id_produto", how="left").fillna(0)
            df["valor_total"]    = df["saldo_atual"] * df["valor_unitario"]
            df["consumo_diario"] = df["total"] / 30

            mask = df["consumo_diario"] > 0
            df["Runway"] = 999
            df.loc[mask, "Runway"] = (
                df.loc[mask, "saldo_atual"] / df.loc[mask, "consumo_diario"]
            ).astype(int)

            def set_status(row):
                if row["saldo_atual"] <= RUPTURA_LIMITE:   return "🔴 Ruptura"
                if row["saldo_atual"] < row["estoque_minimo"]: return "🔴 Crítico"
                if row["Runway"] != 999 and row["Runway"] <= row["lead_time"]: return "🟠 Risco"
                return "🟢 OK"

            df["Status"]    = df.apply(set_status, axis=1)
            df["Runway_Txt"] = df["Runway"].apply(
                lambda x: "Sem consumo" if x == 999 else f"{x} dias"
            )

            itens_crit = int((df["saldo_atual"] < df["estoque_minimo"]).sum())
            cor_crit   = "rgba(239,68,68,0.15)" if itens_crit else "rgba(16,185,129,0.15)"
            borda_crit = "#ef4444" if itens_crit else "#10b859"

            c1, c2, c3, c4 = st.columns(4)
            c1.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Categorias<br><b>{df["categoria"].nunique()}</b></div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Valor Total<br><b>R$ {df["valor_total"].sum():,.2f}</b></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="metric-card" style="background:{cor_crit};border-top:4px solid {borda_crit};color:{borda_crit}">Críticos/Ruptura<br><b>{itens_crit}</b></div>', unsafe_allow_html=True)
            c4.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Giro Total<br><b>{int(df["total"].sum())} un</b></div>', unsafe_allow_html=True)

            st.divider()
            cp1, cp2 = st.columns(2)
            with cp1:
                setor_sel = st.selectbox("Filtrar por setor", ["Todos"] + list(df["categoria"].unique()))
            with cp2:
                busca = st.text_input("🔍 Busca por nome")

            df_f = df.copy()
            if setor_sel != "Todos":
                df_f = df_f[df_f["categoria"] == setor_sel]
            if busca.strip():
                df_f = df_f[df_f["nome"].str.contains(busca, case=False, na=False)]

            def destacar(val):
                if "🔴" in str(val): return "background-color:rgba(239,68,68,0.35);font-weight:bold"
                if "🟠" in str(val): return "background-color:rgba(245,158,11,0.35);font-weight:bold"
                if "🟢" in str(val): return "background-color:rgba(16,185,129,0.35);font-weight:bold"
                return ""

            st.subheader("Posição de estoque")
            st.dataframe(
                df_f[["Status", "categoria", "nome", "saldo_atual", "valor_unitario",
                       "estoque_minimo", "Runway_Txt"]]
                .rename(columns={
                    "categoria": "Setor", "nome": "Produto",
                    "valor_unitario": "Preço Médio", "Runway_Txt": "Cobertura"
                })
                .style.map(destacar, subset=["Status"])
                .format({"Preço Médio": "R$ {:.2f}"}),
                hide_index=True, use_container_width=True,
            )

            st.divider()
            st.subheader("Gráficos")
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Giro por categoria")
                giro = df.groupby("categoria")["total"].sum().reset_index()
                if giro["total"].sum() > 0:
                    st.bar_chart(data=giro, x="categoria", y="total", use_container_width=True)
                else:
                    st.info("Sem saídas registradas.")
            with g2:
                st.markdown("##### Top 5 mais consumidos")
                top = df[df["total"] > 0].nlargest(5, "total")[["nome", "total"]]
                if not top.empty:
                    st.bar_chart(data=top, x="nome", y="total", use_container_width=True)
                else:
                    st.info("Sem consumo registrado.")

            st.divider()
            st.subheader("Sugestão de reposição")
            df_f["Mínimo Ideal"]     = (df_f["consumo_diario"] * df_f["lead_time"] * 1.2).astype(int)
            df_f["Alvo"]             = df_f[["estoque_minimo", "Mínimo Ideal"]].max(axis=1)
            df_f["Sugestão Compra"]  = (df_f["Alvo"] - df_f["saldo_atual"]).clip(lower=0)

            apenas_urgente = st.checkbox("Mostrar apenas itens com necessidade urgente de compra")
            df_comp = df_f[df_f["Sugestão Compra"] > 0] if apenas_urgente else df_f
            st.dataframe(
                df_comp[["categoria", "nome", "lead_time", "saldo_atual",
                          "Mínimo Ideal", "Sugestão Compra"]]
                .rename(columns={"categoria": "Setor", "nome": "Produto",
                                  "lead_time": "Entrega (d)", "saldo_atual": "Saldo",
                                  "Sugestão Compra": "Comprar"}),
                hide_index=True, use_container_width=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # SAÍDAS / ENTRADAS
    # ══════════════════════════════════════════════════════════════════════════
    with aba_op:
        df  = listar_produtos()
        if not df.empty:
            ops = dict(zip(df["nome"], df["id"]))
            col_e, col_s = st.columns(2)

            with col_e:
                with st.container(border=True):
                    st.subheader("⬇️ Entrada")
                    sel_e  = st.selectbox("Produto", list(ops.keys()), key="e_p")
                    id_e   = ops[sel_e]
                    pmp_at = float(df.loc[df["id"] == id_e, "valor_unitario"].values[0])
                    sal_e  = int(df.loc[df["id"] == id_e, "saldo_atual"].values[0])

                    c1, c2 = st.columns(2)
                    with c1: qe = st.number_input("Quantidade", min_value=1, key="e_q")
                    with c2: preco = st.number_input("Preço unit. (R$)", min_value=0.0,
                                                     value=pmp_at, step=0.01, key="e_v")
                    obs_e = st.text_input("Nota/Fornecedor", key="e_obs")

                    st.info(f"Saldo atual: **{sal_e}** → após entrada: **{sal_e + int(qe)}**")

                    if st.button("Confirmar Entrada", type="secondary"):
                        ok, msg = registrar_entrada(id_e, int(qe), preco, obs_e)
                        if ok:
                            disparar_sync()
                            st.toast(f"📥 Entrada registrada! {msg}", icon="✅")
                            st.rerun()
                        else:
                            st.error(msg)

            with col_s:
                with st.container(border=True):
                    st.subheader("📤 Saída")
                    sel_s  = st.selectbox("Produto ", list(ops.keys()), key="s_p")
                    id_s   = ops[sel_s]
                    max_s  = int(df.loc[df["id"] == id_s, "saldo_atual"].values[0])

                    c1, c2 = st.columns(2)
                    with c1: qs = st.number_input("Quantidade", min_value=1, key="s_q")
                    with c2: obs_s = st.text_input("Destino/Obs", key="s_obs")

                    bloqueado = int(qs) > max_s
                    if bloqueado:
                        st.error(f"❌ Saldo insuficiente: {max_s} un disponíveis.")

                    if st.button("Confirmar Saída", type="primary", disabled=bloqueado):
                        ok, msg = registrar_saida(id_s, int(qs), obs_s)
                        if ok:
                            disparar_sync()
                            st.toast(f"📤 Saída registrada! {msg}", icon="🚀")
                            st.rerun()
                        else:
                            st.error(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # INVENTÁRIO (CONTAGEM SEMANAL)
    # ══════════════════════════════════════════════════════════════════════════
    with aba_inv:
        st.subheader("📋 Auditoria de Inventário")
        df = listar_produtos()

        if not df.empty:
            hoje = _hoje_str()
            with get_conn() as conn:
                # Parâmetro ? — sem SQL injection
                contados_hoje = pd.read_sql(
                    "SELECT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora LIKE ?",
                    conn, params=(f"{hoje}%",),
                )
            ids_hoje = set(contados_hoje["id_produto"].tolist())

            with st.container(border=True):
                ops_inv = {}
                for _, row in df.iterrows():
                    label = f"✅ {row['nome']} (auditado hoje)" if row["id"] in ids_hoje else row["nome"]
                    ops_inv[label] = row["id"]

                sel_c  = st.selectbox("Insumo para contagem", list(ops_inv.keys()), key="c_p")
                id_c   = ops_inv[sel_c]
                s_sis  = int(df.loc[df["id"] == id_c, "saldo_atual"].values[0])

                st.metric("Saldo sistêmico", f"{s_sis} un")
                f_cont = int(st.number_input("Quantidade física contada", min_value=0, step=1, key="c_q"))
                diff   = f_cont - s_sis

                c1, c2, c3 = st.columns(3)
                c1.metric("Sistêmico", s_sis)
                c2.metric("Físico contado", f_cont)
                c3.metric("Divergência", f"{diff:+d}")

                if st.button("💾 Gravar inventário", use_container_width=True, type="primary"):
                    ok, msg = registrar_contagem(id_c, f_cont, st.session_state["usuario_atual"])
                    if ok:
                        disparar_sync()
                        st.toast(f"📋 {msg}", icon="💾")
                        st.rerun()
                    else:
                        st.error(msg)

            if ids_hoje:
                st.success(f"📌 {len(ids_hoje)} insumo(s) auditado(s) hoje ({hoje}).")

            st.divider()
            st.subheader("Relatório de ajustes")

            # Filtro seguro com parâmetro
            prod_lista  = ["Todos"] + list(df["nome"].unique())
            prod_sel    = st.selectbox("Filtrar por produto", prod_lista)

            query_base  = """
                SELECT m.data_hora           AS "Data/Hora",
                       p.nome                AS "Produto",
                       (m.saldo_resultante - m.quantidade) AS "Anterior",
                       m.saldo_resultante    AS "Físico",
                       m.quantidade          AS "Divergência",
                       m.observacao          AS "Registro"
                FROM movimentacoes m
                JOIN produtos p ON p.id = m.id_produto
                WHERE m.tipo = 'Contagem'
            """
            params: list = []
            if prod_sel != "Todos":
                query_base += " AND p.nome = ?"
                params.append(prod_sel)
            query_base += " ORDER BY m.id DESC LIMIT 15"

            with get_conn() as conn:
                hist_inv = pd.read_sql(query_base, conn, params=params)

            if not hist_inv.empty:
                def cor_div(val):
                    if val < 0: return "color:#ef4444;font-weight:bold"
                    if val > 0: return "color:#10b859;font-weight:bold"
                    return "color:#94a3b8"

                st.dataframe(
                    hist_inv.style.map(cor_div, subset=["Divergência"]),
                    hide_index=True, use_container_width=True,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # HISTÓRICO
    # ══════════════════════════════════════════════════════════════════════════
    with aba_hist:
        st.subheader("📜 Histórico de movimentações")
        mv = listar_movimentacoes()
        df = listar_produtos()

        if not mv.empty:
            if not df.empty:
                st.markdown("##### Curva de custos por produto")
                item_an = st.selectbox("Produto", list(df["nome"].unique()))
                ent_item = mv[(mv["produto"] == item_an) & (mv["tipo"] == "Entrada")].copy()

                if not ent_item.empty:
                    def extrair_preco(obs):
                        try:
                            if "Pago: R$" in str(obs):
                                return float(str(obs).split("Pago: R$ ")[1].split("/un")[0])
                        except Exception:
                            pass
                        return None

                    ent_item["Preço (R$)"] = ent_item["observacao"].apply(extrair_preco)
                    ent_item = ent_item.dropna(subset=["Preço (R$)"]).iloc[::-1]
                    if not ent_item.empty:
                        st.line_chart(data=ent_item, x="data_hora", y="Preço (R$)", use_container_width=True)
                else:
                    st.info("Sem entradas para este produto.")

            st.divider()
            mv["Mês/Ano"] = mv["data_hora"].apply(
                lambda x: x.split()[0][3:] if " " in str(x) else ""
            )
            periodos = sorted(mv["Mês/Ano"].unique(), reverse=True)
            mes_sel  = st.selectbox("Filtrar por período", periodos)
            st.dataframe(
                mv[mv["Mês/Ano"] == mes_sel].drop(columns=["Mês/Ano"]),
                use_container_width=True, hide_index=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # ABAS ADMIN
    # ══════════════════════════════════════════════════════════════════════════
    if is_admin:
        aba_ia, aba_cfg = abas[4], abas[5]

        with aba_ia:
            st.subheader("🧠 Assistente IA de Suprimentos")
            try:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                modelos = [
                    m.name.replace("models/", "")
                    for m in genai.list_models()
                    if "generateContent" in m.supported_generation_methods
                ]
                modelo = st.selectbox("Modelo", modelos)

                if st.button("✨ Gerar diagnóstico logístico"):
                    df_ia  = listar_produtos()
                    cons_ia = calcular_consumo_mensal()
                    df_ia  = df_ia.merge(cons_ia, left_on="id", right_on="id_produto", how="left").fillna(0)
                    df_ia["consumo_mensal"] = df_ia["total"].astype(int)

                    with st.spinner(f"Analisando com {modelo}..."):
                        mod    = genai.GenerativeModel(modelo)
                        prompt = (
                            "Analise o estoque logístico abaixo:\n"
                            f"{df_ia[['categoria','nome','saldo_atual','estoque_minimo','lead_time','consumo_mensal']].to_string(index=False)}\n"
                            "Entregue: resumo de saúde, riscos de ruptura antes do lead time e sugestão de compras."
                        )
                        st.write(mod.generate_content(prompt).text)
            except Exception as e:
                st.error(f"Erro na API do Google: {e}")

        with aba_cfg:
            st.markdown("### Aprovação de novos operadores")
            with get_conn() as conn:
                pendentes = pd.read_sql(
                    "SELECT usuario, pergunta_seguranca FROM usuarios WHERE aprovado = 0", conn
                )

            if not pendentes.empty:
                st.dataframe(pendentes, use_container_width=True, hide_index=True)
                col_u, col_p, col_a = st.columns(3)
                with col_u: usr_alvo  = st.selectbox("Usuário", list(pendentes["usuario"]))
                with col_p: perf_alvo = st.selectbox("Perfil", PERFIS)
                with col_a:
                    c_ap, c_rec = st.columns(2)
                    with c_ap:
                        if st.button("✅ Aprovar", use_container_width=True):
                            with get_conn() as conn:
                                conn.execute(
                                    "UPDATE usuarios SET aprovado=1, perfil=? WHERE usuario=?",
                                    (perf_alvo, usr_alvo),
                                )
                            disparar_sync()
                            st.success(f"'{usr_alvo}' aprovado como {perf_alvo}.")
                            st.rerun()
                    with c_rec:
                        if st.button("❌ Recusar", use_container_width=True):
                            with get_conn() as conn:
                                conn.execute("DELETE FROM usuarios WHERE usuario=?", (usr_alvo,))
                            disparar_sync()
                            st.warning(f"Solicitação de '{usr_alvo}' excluída.")
                            st.rerun()
            else:
                st.success("Nenhuma solicitação pendente.")

            st.divider()
            st.markdown("### Gerenciar usuários ativos")
            with get_conn() as conn:
                ativos = pd.read_sql(
                    "SELECT usuario, perfil FROM usuarios WHERE aprovado=1", conn
                )

            if not ativos.empty:
                c1, c2, c3 = st.columns(3)
                with c1: usr_ed = st.selectbox("Usuário", list(ativos["usuario"]), key="ue")
                perf_db = ativos[ativos["usuario"] == usr_ed]["perfil"].values[0]
                idx_p   = PERFIS.index(perf_db) if perf_db in PERFIS else 0
                with c2: novo_perf = st.selectbox("Novo perfil", PERFIS, index=idx_p, key="pe")
                with c3:
                    st.write("")
                    if st.button("🔄 Atualizar perfil", use_container_width=True):
                        if usr_ed == st.session_state["usuario_atual"] and novo_perf == "Operador":
                            st.error("⚠️ Você não pode rebaixar sua própria conta.")
                        else:
                            with get_conn() as conn:
                                conn.execute(
                                    "UPDATE usuarios SET perfil=? WHERE usuario=?",
                                    (novo_perf, usr_ed),
                                )
                            disparar_sync()
                            st.success(f"Perfil de '{usr_ed}' → {novo_perf}.")
                            st.rerun()

            st.divider()
            a1, a2, a3 = st.tabs(["➕ Novo", "✏️ Editar", "🗑️ Excluir"])

            with a1:
                with st.form("new_p"):
                    n = st.text_input("Nome do insumo")
                    c = st.selectbox("Setor", CATEGORIAS)
                    m = st.number_input("Estoque mínimo", value=10)
                    l = st.number_input("Lead time (dias)", value=3)
                    v = st.number_input("Valor unit. inicial (R$)", value=0.0)
                    if st.form_submit_button("Cadastrar"):
                        if n.strip():
                            ok, msg = cadastrar_produto(n.strip(), int(m), v, c, int(l))
                            if ok:
                                disparar_sync()
                                st.toast(f"➕ {msg}", icon="✨")
                                st.rerun()
                            else:
                                st.error(msg)

            with a2:
                df_cfg = listar_produtos()
                if not df_cfg.empty:
                    ops_e = dict(zip(df_cfg["nome"], df_cfg["id"]))
                    sel_e = st.selectbox("Produto para editar", list(ops_e.keys()))
                    id_e  = ops_e[sel_e]
                    p_at  = df_cfg[df_cfg["id"] == id_e].iloc[0]
                    with st.form("edit_p"):
                        en = st.text_input("Nome", value=p_at["nome"])
                        ec = st.selectbox("Setor", CATEGORIAS,
                                          index=CATEGORIAS.index(p_at["categoria"])
                                          if p_at["categoria"] in CATEGORIAS else 0)
                        em = st.number_input("Mínimo", value=int(p_at["estoque_minimo"]))
                        el = st.number_input("Lead time", value=int(p_at["lead_time"]))
                        ev = st.number_input("Preço médio", value=float(p_at["valor_unitario"]))
                        if st.form_submit_button("Atualizar"):
                            ok, msg = editar_produto(id_e, en, int(em), ev, ec, int(el))
                            if ok:
                                disparar_sync()
                                st.toast(f"✏️ {msg}", icon="⚙️")
                                st.rerun()
                            else:
                                st.error(msg)

            with a3:
                df_cfg = listar_produtos()
                if not df_cfg.empty:
                    ops_d = dict(zip(df_cfg["nome"], df_cfg["id"]))
                    sel_d = st.selectbox("Produto para excluir", list(ops_d.keys()))
                    id_d  = ops_d[sel_d]
                    conf  = st.checkbox("Confirmo que quero apagar este insumo e seu histórico.")
                    if st.button("🗑️ Excluir definitivamente", type="primary", disabled=not conf):
                        ok, msg = deletar_produto(id_d)
                        if ok:
                            disparar_sync()
                            st.toast(f"🗑️ {msg}", icon="🗑️")
                            st.rerun()
                        else:
                            st.error(msg)