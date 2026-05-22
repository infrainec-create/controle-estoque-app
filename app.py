import streamlit as st
import sqlite3
from datetime import datetime, timedelta
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
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH        = "estoque.db"
TIMEZONE       = "America/Fortaleza"
FMT_DATETIME   = "%d/%m/%Y %H:%M"
FMT_DATE       = "%d/%m/%Y"
CACHE_TTL      = 30
CATEGORIAS     = ["Limpeza", "Copa", "EPI", "Escritório", "Geral"]
PERFIS         = ["Operador", "Administrador"]
RUPTURA_LIMITE = 0
SESSION_TTL_H  = 8    # Horas até o token expirar
MAX_TENTATIVAS = 5    # Tentativas antes do bloqueio
BLOQUEIO_MIN   = 30   # Minutos de cooldown

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wms")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="WMS 4.0", page_icon="📦", layout="wide")
st.markdown("""
<style>
.stButton>button { border-radius:10px; font-weight:600; height:3em; width:100%; margin-top:10px; }
.metric-card { padding:20px; border-radius:12px; box-shadow:0 4px 6px rgba(0,0,0,.1); margin-bottom:15px; }
#MainMenu { visibility:hidden; }
footer     { visibility:hidden; }
</style>
""", unsafe_allow_html=True)

FOLDER_ID = st.secrets["FOLDER_ID"]

# ─────────────────────────────────────────────────────────────────────────────
# BANCO DE DADOS
# ─────────────────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        # 1. Cria as tabelas se não existirem
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                data_criacao TEXT NOT NULL,
                expira_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tentativas_login (
                usuario TEXT PRIMARY KEY,
                contador INTEGER DEFAULT 0,
                ultimo_erro TEXT
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                senha_hash TEXT NOT NULL,
                pergunta_seguranca TEXT NOT NULL,
                resposta_seguranca_hash TEXT NOT NULL,
                aprovado INTEGER DEFAULT 0,
                perfil TEXT DEFAULT 'Operador'
            );
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
        
        # 2. Garante que a coluna expira_em exista na tabela sessoes
        try:
            conn.execute("ALTER TABLE sessoes ADD COLUMN expira_em TEXT DEFAULT '2099-01-01 00:00'")
        except sqlite3.OperationalError:
            pass # Coluna já existe

        # 3. Garante colunas de perfil e aprovado
        for ddl in [
            "ALTER TABLE usuarios ADD COLUMN aprovado INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'Operador'"
        ]:
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        
        conn.execute("UPDATE usuarios SET perfil='Administrador' WHERE usuario='admin'")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DE DATA/HORA
# ─────────────────────────────────────────────────────────────────────────────
def _now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))

def _now_str() -> str:
    return _now_dt().strftime(FMT_DATETIME)

def _hoje_str() -> str:
    return _now_dt().strftime(FMT_DATE)

def _dt_str(dt: datetime) -> str:
    return dt.strftime(FMT_DATETIME)

def _str_dt(s: str) -> datetime:
    return datetime.strptime(s, FMT_DATETIME).replace(tzinfo=ZoneInfo(TIMEZONE))


# ─────────────────────────────────────────────────────────────────────────────
# SEGURANÇA
# ─────────────────────────────────────────────────────────────────────────────
def hash_senha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ── Sessão com TTL ───────────────────────────────────────────────────────────
def _criar_sessao(usuario: str) -> str:
    token  = str(uuid.uuid4())
    agora  = _now_dt()
    expira = agora + timedelta(hours=SESSION_TTL_H)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sessoes WHERE usuario=? OR expira_em < ?",
            (usuario, _dt_str(agora)),
        )
        conn.execute(
            "INSERT INTO sessoes (token, usuario, data_criacao, expira_em) VALUES (?,?,?,?)",
            (token, usuario, _dt_str(agora), _dt_str(expira)),
        )
    return token


def _validar_token(token: str):
    agora = _dt_str(_now_dt())
    with get_conn() as conn:
        return conn.execute(
            """SELECT s.usuario, u.perfil, s.expira_em
               FROM sessoes s JOIN usuarios u ON s.usuario=u.usuario
               WHERE s.token=? AND s.expira_em > ?""",
            (token, agora),
        ).fetchone()


def _revogar_token(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessoes WHERE token=?", (token,))


# ── Rate limiting ────────────────────────────────────────────────────────────
def _verificar_bloqueio(usuario: str) -> tuple[bool, int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT contador, ultimo_erro FROM tentativas_login WHERE usuario=?",
            (usuario,),
        ).fetchone()
    if not row or row[0] < MAX_TENTATIVAS or not row[1]:
        return False, 0
    try:
        bloqueado_ate = _str_dt(row[1]) + timedelta(minutes=BLOQUEIO_MIN)
        agora = _now_dt()
        if agora < bloqueado_ate:
            return True, int((bloqueado_ate - agora).total_seconds() / 60) + 1
        with get_conn() as conn:
            conn.execute("DELETE FROM tentativas_login WHERE usuario=?", (usuario,))
        return False, 0
    except Exception:
        return False, 0


def _registrar_falha(usuario: str) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tentativas_login (usuario, contador, ultimo_erro) VALUES (?,1,?)
               ON CONFLICT(usuario) DO UPDATE SET contador=contador+1, ultimo_erro=excluded.ultimo_erro""",
            (usuario, _now_str()),
        )
        return conn.execute(
            "SELECT contador FROM tentativas_login WHERE usuario=?", (usuario,)
        ).fetchone()[0]


def _zerar_tentativas(usuario: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tentativas_login WHERE usuario=?", (usuario,))


def _login(usuario: str, senha: str) -> tuple[bool, str, str]:
    bloqueado, mins = _verificar_bloqueio(usuario)
    if bloqueado:
        return False, f"bloqueado:{mins}", ""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT aprovado, perfil FROM usuarios WHERE usuario=? AND senha_hash=?",
            (usuario, hash_senha(senha)),
        ).fetchone()
    if not row:
        restam = max(0, MAX_TENTATIVAS - _registrar_falha(usuario))
        return False, f"invalido:{restam}", ""
    if row[0] != 1:
        return False, "pendente", ""
    _zerar_tentativas(usuario)
    return True, usuario, row[1]


# ─────────────────────────────────────────────────────────────────────────────
# CACHE E QUERIES
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=CACHE_TTL)
def listar_produtos() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)


@st.cache_data(ttl=CACHE_TTL)
def listar_movimentacoes() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id, p.nome AS produto, m.data_hora, m.tipo,
                   m.quantidade, m.saldo_resultante, m.observacao
            FROM movimentacoes m
            JOIN produtos p ON p.id=m.id_produto
            ORDER BY m.id DESC
        """, conn)


@st.cache_data(ttl=CACHE_TTL)
def calcular_consumo_mensal() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id_produto, SUM(ABS(quantidade)) AS total
            FROM movimentacoes
            WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0)
            GROUP BY id_produto
        """, conn)


def invalidar_cache() -> None:
    listar_produtos.clear()
    listar_movimentacoes.clear()
    calcular_consumo_mensal.clear()


# ─────────────────────────────────────────────────────────────────────────────
# LÓGICA DE NEGÓCIO
# ─────────────────────────────────────────────────────────────────────────────
def _reg_mov(conn, id_produto, tipo, quantidade, saldo_resultante, obs=""):
    conn.execute(
        "INSERT INTO movimentacoes (id_produto,data_hora,tipo,quantidade,saldo_resultante,observacao) VALUES (?,?,?,?,?,?)",
        (id_produto, _now_str(), tipo, quantity := quantidade, saldo_resultante, obs),
    )


def registrar_entrada(id_produto, quantidade, preco_compra, obs=""):
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT saldo_atual, valor_unitario FROM produtos WHERE id=?", (id_produto,)
            ).fetchone()
            if not row:
                return False, "Produto não encontrado."
            saldo_ant, pmp_ant = row
            total_novo = saldo_ant + quantidade
            
            # Trava de segurança para cálculo do PMP coerente
            if total_novo > 0:
                novo_pmp = ((saldo_ant * pmp_ant) + (quantidade * preco_compra)) / total_novo
            else:
                novo_pmp = preco_compra
                
            conn.execute(
                "UPDATE produtos SET saldo_atual=saldo_atual+?, valor_unitario=? WHERE id=?",
                (quantidade, novo_pmp, id_produto),
            )
            nota = f"{obs} | Pago: R$ {preco_compra:.2f}/un".strip(" |") if obs else f"Pago: R$ {preco_compra:.2f}/un"
            _reg_mov(conn, id_produto, "Entrada", quantidade, total_novo, nota)
        return True, f"Novo PMP: R$ {novo_pmp:.2f}"
    except Exception as e:
        log.error("registrar_entrada: %s", e)
        return False, str(e)


def registrar_saida(id_produto, quantidade, obs=""):
    try:
        with get_conn() as conn:
            saldo = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            if quantidade > saldo:
                return False, f"Estoque insuficiente. Saldo: {saldo}"
            novo = saldo - quantidade
            conn.execute("UPDATE produtos SET saldo_atual=saldo_atual-? WHERE id=?", (quantidade, id_produto))
            _reg_mov(conn, id_produto, "Saída", -quantidade, novo, obs)
        return True, f"Novo saldo: {novo}"
    except Exception as e:
        log.error("registrar_saida: %s", e)
        return False, str(e)


def registrar_contagem(id_produto, fisico, operador):
    try:
        with get_conn() as conn:
            sis  = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            diff = fisico - sis
            conn.execute("UPDATE produtos SET saldo_atual=? WHERE id=?", (fisico, id_produto))
            _reg_mov(conn, id_produto, "Contagem", diff, fisico, f"Inventário | Op: {operador}")
        return True, f"Divergência: {diff:+d} un. Novo saldo: {fisico}"
    except Exception as e:
        log.error("registrar_contagem: %s", e)
        return False, str(e)


def registrar_ajuste(id_produto, novo_saldo, motivo=""):
    try:
        with get_conn() as conn:
            ant = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            conn.execute("UPDATE produtos SET saldo_atual=? WHERE id=?", (novo_saldo, id_produto))
            _reg_mov(conn, id_produto, "Ajuste", novo_saldo - ant, novo_saldo, motivo)
        return True, f"Saldo adjusted para {novo_saldo}"
    except Exception as e:
        log.error("registrar_ajuste: %s", e)
        return False, str(e)


def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO produtos (nome,saldo_atual,estoque_minimo,valor_unitario,categoria,lead_time) VALUES (?,0,?,?,?,?)",
                (nome, estoque_minimo, valor_unitario, categoria, lead_time),
            )
        return True, f'"{nome}" cadastrado.'
    except sqlite3.IntegrityError:
        return False, f'"{nome}" já existe.'
    except Exception as e:
        return False, str(e)


def editar_produto(id_p, nome, min_e, valor, cat, lead):
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE produtos SET nome=?,estoque_minimo=?,valor_unitario=?,categoria=?,lead_time=? WHERE id=?",
                (nome, min_e, valor, cat, lead, id_p),
            )
        return True, "Atualizado."
    except Exception as e:
        return False, str(e)


def deletar_produto(id_produto):
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM movimentacoes WHERE id_produto=?", (id_produto,))
            conn.execute("DELETE FROM produtos WHERE id=?", (id_produto,))
        return True, "Removido."
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE
# ─────────────────────────────────────────────────────────────────────────────
def _drive_svc():
    creds = service_account.Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]))
    return build("drive", "v3", credentials=creds)


def _upsert_drive(svc, name, media):
    q = f"name='{name}' and '{FOLDER_ID}' in parents and trashed=false"
    files = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    if files:
        svc.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        svc.files().create(body={"name": name, "parents": [FOLDER_ID]}, media_body=media).execute()


def _executar_sync():
    backup_file = "estoque_backup.db"
    try:
        svc = _drive_svc()
        
        # Correção Crítica: Faz cópia segura via Backup nativo para não prender o WAL ativo
        with get_conn() as conn:
            bck = sqlite3.connect(backup_file)
            conn.backup(bck)
            bck.close()
            
        _upsert_drive(svc, DB_PATH, MediaFileUpload(backup_file, mimetype="application/x-sqlite3", resumable=True))
        
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs  = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo,
                       m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m JOIN produtos p ON p.id=m.id_produto ORDER BY m.id DESC
            """, conn)
            
        for df_e, fname in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            _upsert_drive(svc, fname,
                MediaIoBaseUpload(BytesIO(df_e.to_csv(index=False).encode("utf-8-sig")), mimetype="text/csv"))
                
        st.session_state["ultima_sync"] = _now_str()
        st.session_state["sync_erro"]   = None
    except Exception as e:
        log.error("sync Drive: %s", e)
        st.session_state["sync_erro"] = str(e)
    finally:
        if os.path.exists(backup_file):
            try:
                os.remove(backup_file)
            except Exception:
                pass


def disparar_sync():
    invalidar_cache()
    threading.Thread(target=_executar_sync, daemon=True).start()


def descarregar_do_drive():
    try:
        svc = _drive_svc()
        res = svc.files().list(
            q=f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false",
            fields="files(id)",
        ).execute()
        if res.get("files"):
            req = svc.files().get_media(fileId=res["files"][0]["id"])
            with open(DB_PATH, "wb") as f:
                dl = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            return True
    except Exception as e:
        log.error("descarregar Drive: %s", e)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# INICIALIZAÇÃO
# ─────────────────────────────────────────────────────────────────────────────
if "db_ok" not in st.session_state:
    if not os.path.exists(DB_PATH):
        descarregar_do_drive()
    init_db()
    st.session_state.update(db_ok=True, ultima_sync=None, sync_erro=None)

if "autenticado" not in st.session_state:
    st.session_state.update(autenticado=False, usuario_atual="", perfil_atual="", token_expira="")
    token = st.query_params.get("token")
    if token:
        sessao = _validar_token(token)
        if sessao:
            st.session_state.update(
                autenticado=True,
                usuario_atual=sessao[0],
                perfil_atual=sessao[1],
                token_expira=sessao[2],
            )
        else:
            st.query_params.clear()   # token expirado — limpa URL

# ─────────────────────────────────────────────────────────────────────────────
# FLUXO DE LOGIN
# ─────────────────────────────────────────────────────────────────────────────
if not st.session_state["autenticado"]:
    st.title("🔒 WMS — Controle de Acesso")
    aba_login, aba_cadastro, aba_recuperar = st.tabs(["🔑 Entrar", "👤 Criar Conta", "🛠️ Esqueci a Senha"])

    # ── Login com rate limiting ──────────────────────────────────────────────
    with aba_login:
        with st.form("form_login"):
            usr = st.text_input("Usuário").strip()
            pwd = st.text_input("Senha", type="password")
            btn = st.form_submit_button("Acessar WMS")
            if btn:
                if usr and pwd:
                    ok, u, perfil = _login(usr, pwd)
                    if ok:
                        token = _criar_sessao(u)
                        st.query_params["token"] = token
                        sessao = _validar_token(token)
                        st.session_state.update(
                            autenticado=True, usuario_atual=u,
                            perfil_atual=perfil,
                            token_expira=sessao[2] if sessao else "",
                        )
                        st.toast(f"Bem-vindo, {u}!", icon="👋")
                        st.rerun()
                    elif u.startswith("bloqueado:"):
                        mins = u.split(":")[1]
                        st.error(f"🔒 Conta bloqueada por excesso de tentativas. Aguarde {mins} minuto(s).")
                    elif u.startswith("invalido:"):
                        restam = int(u.split(":")[1])
                        if restam > 0:
                            st.error(f"❌ Usuário ou senha incorretos. {restam} tentativa(s) restante(s).")
                        else:
                            st.error(f"🔒 Limite atingido. Tente novamente em {BLOQUEIO_MIN} minutos.")
                    elif u == "pendente":
                        st.error("⏳ Cadastro aguardando aprovação do administrador.")
                else:
                    st.warning("Preencha todos os campos.")

    # ── Cadastro ─────────────────────────────────────────────────────────────
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
                    is_admin   = new_usr.lower() == "admin"
                    aprovado   = 1 if is_admin else 0
                    perfil_ini = "Administrador" if is_admin else "Operador"
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                "INSERT INTO usuarios (usuario,senha_hash,pergunta_seguranca,resposta_seguranca_hash,aprovado,perfil) VALUES (?,?,?,?,?,?)",
                                (new_usr, hash_senha(new_pwd), pergunta, hash_senha(resposta), aprovado, perfil_ini),
                            )
                        disparar_sync()
                        st.success("👑 Admin criado! Faça o login." if is_admin else f"⏳ Solicitação de '{new_usr}' enviada.")
                    except sqlite3.IntegrityError:
                        st.error("Esse nome de usuário já existe.")
                else:
                    st.warning("Todos os campos são obrigatórios.")

    # ── Recuperação de senha ─────────────────────────────────────────────────
    with aba_recuperar:
        usr_rec = st.text_input("Usuário para redefinir").strip()
        if usr_rec:
            with get_conn() as conn:
                row = conn.execute("SELECT pergunta_seguranca FROM usuarios WHERE usuario=?", (usr_rec,)).fetchone()
            if row:
                st.info(f"Pergunta: **{row[0]}**")
                resp    = st.text_input("Resposta", type="password").strip().lower()
                new_pwd = st.text_input("Nova senha", type="password")
                if st.button("Gravar nova senha"):
                    if resp and new_pwd:
                        with get_conn() as conn:
                            ok = conn.execute(
                                "SELECT 1 FROM usuarios WHERE usuario=? AND resposta_seguranca_hash=?",
                                (usr_rec, hash_senha(resp)),
                            ).fetchone()
                        if ok:
                            with get_conn() as conn:
                                conn.execute("UPDATE usuarios SET senha_hash=? WHERE usuario=?", (hash_senha(new_pwd), usr_rec))
                                conn.execute("DELETE FROM sessoes WHERE usuario=?", (usr_rec,))
                            disparar_sync()
                            st.success("✅ Senha redefinida! Todas as sessões ativas foram encerradas.")
                        else:
                            st.error("❌ Resposta incorreta.")
                    else:
                        st.warning("Preencha a resposta e a nova senha.")
            else:
                st.error("Usuário não encontrado.")

# ─────────────────────────────────────────────────────────────────────────────
# APP PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
else:
    is_admin    = st.session_state["perfil_atual"] == "Administrador"
    token_atual = st.query_params.get("token", "")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.write(f"👤 **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ {st.session_state['perfil_atual']}")
        if st.session_state.get("token_expira"):
            st.caption(f"⏱️ Sessão expira: {st.session_state['token_expira']}")
        st.divider()
        if st.session_state.get("sync_erro"):
            st.warning(f"⚠️ Falha sync: {st.session_state['sync_erro']}")
        elif st.session_state.get("ultima_sync"):
            st.caption(f"☁️ Sync: {st.session_state['ultima_sync']}")
        else:
            st.caption("☁️ Sync pendente...")
        if st.button("🔄 Sincronizar agora"):
            disparar_sync()
            st.toast("Sincronização iniciada!", icon="☁️")
        st.divider()
        if st.button("🚪 Sair", type="primary"):
            if token_atual:
                _revogar_token(token_atual)
                st.query_params.clear()
            st.session_state.update(autenticado=False, usuario_atual="", perfil_atual="", token_expira="")
            st.rerun()

    # ── Abas ──────────────────────────────────────────────────────────────────
    nomes_abas = ["📊 Painel", "⚡ Saídas/Entradas", "📋 Inventário", "📜 Histórico"]
    if is_admin:
        nomes_abas += ["🧠 IA Analista", "⚙️ Config"]
    abas = st.tabs(nomes_abas)
    aba_painel, aba_op, aba_inv, aba_hist = abas[:4]

    # ══════════════════════════════════════════════════════════════════════════
    # PAINEL
    # ══════════════════════════════════════════════════════════════════════════
    with aba_painel:
        df = listar_produtos()
        cons = calcular_consumo_mensal()
        
        if not df.empty:
            df = df.merge(cons, left_on="id", right_on="id_produto", how="left").fillna(0)
            
            df["valor_total"]    = df["saldo_atual"] * df["valor_unitario"]
            df["consumo_diario"] = df["total"] / 30
            mask = df["consumo_diario"] > 0
            df["Runway"] = 999
            df.loc[mask, "Runway"] = (df.loc[mask, "saldo_atual"] / df.loc[mask, "consumo_diario"]).astype(int)

            def set_status(row):
                lead = row.get("lead_time", 3)
                if row["saldo_atual"] <= RUPTURA_LIMITE:           return "🔴 Ruptura"
                if row["saldo_atual"] < row["estoque_minimo"]:   return "🔴 Crítico"
                if row["Runway"] != 999 and row["Runway"] <= lead: return "🟠 Risco"
                return "🟢 OK"

            df["Status"]     = df.apply(set_status, axis=1)
            df["Runway_Txt"] = df["Runway"].apply(lambda x: "Sem consumo" if x == 999 else f"{x} dias")

            itens_crit = int((df["saldo_atual"] < df["estoque_minimo"]).sum())
            cor = "#ef4444" if itens_crit else "#10b859"
            bg  = "rgba(239,68,68,0.15)" if itens_crit else "rgba(16,185,129,0.15)"

            c1, c2, c3, c4 = st.columns(4)
            c1.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Categorias<br><b>{df["categoria"].nunique()}</b></div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Valor Total<br><b>R$ {df["valor_total"].sum():,.2f}</b></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="metric-card" style="background:{bg};border-top:4px solid {cor};color:{cor}">Críticos/Ruptura<br><b>{itens_crit}</b></div>', unsafe_allow_html=True)
            c4.markdown(f'<div class="metric-card" style="border-top:4px solid #0052cc">Giro Total<br><b>{int(df["total"].sum())} un</b></div>', unsafe_allow_html=True)

            st.divider()
            cp1, cp2 = st.columns(2)
            with cp1: setor_sel = st.selectbox("Filtrar por setor", ["Todos"] + list(df["categoria"].unique()))
            with cp2: busca = st.text_input("🔍 Busca por nome")

            df_f = df.copy()
            if setor_sel != "Todos": df_f = df_f[df_f["categoria"] == setor_sel]
            if busca.strip(): df_f = df_f[df_f["nome"].str.contains(busca, case=False, na=False)]

            def destacar(val):
                if "🔴" in str(val): return "background-color:rgba(239,68,68,0.35);font-weight:bold"
                if "🟠" in str(val): return "background-color:rgba(245,158,11,0.35);font-weight:bold"
                if "🟢" in str(val): return "background-color:rgba(16,185,129,0.35);font-weight:bold"
                return ""

            st.subheader("Posição de estoque")
            st.dataframe(
                df_f[["Status","categoria","nome","saldo_atual","valor_unitario","estoque_minimo","Runway_Txt"]]
                .rename(columns={"categoria":"Setor","nome":"Produto","valor_unitario":"Preço Médio","Runway_Txt":"Cobertura"})
                .style.map(destacar, subset=["Status"]).format({"Preço Médio":"R$ {:.2f}"}),
                hide_index=True, use_container_width=True,
            )

            st.divider()
            st.subheader("Gráficos")
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Giro por categoria")
                giro = df.groupby("categoria")["total"].sum().reset_index()
                st.bar_chart(data=giro, x="categoria", y="total", use_container_width=True) if giro["total"].sum() > 0 else st.info("Sem saídas.")
            with g2:
                st.markdown("##### Top 5 mais consumidos")
                top = df[df["total"] > 0].nlargest(5, "total")[["nome","total"]]
                st.bar_chart(data=top, x="nome", y="total", use_container_width=True) if not top.empty else st.info("Sem consumo.")

            st.divider()
            st.subheader("Sugestão de reposição")
            df_f["Mínimo Ideal"]    = (df_f["consumo_diario"] * df_f["lead_time"] * 1.2).astype(int)
            df_f["Alvo"]            = df_f[["estoque_minimo","Mínimo Ideal"]].max(axis=1)
            df_f["Sugestão Compra"] = (df_f["Alvo"] - df_f["saldo_atual"]).clip(lower=0)
            urgente = st.checkbox("Mostrar apenas itens com necessidade urgente de compra")
            df_comp = df_f[df_f["Sugestão Compra"] > 0] if urgente else df_f
            st.dataframe(
                df_comp[["categoria","nome","lead_time","saldo_atual","Mínimo Ideal","Sugestão Compra"]]
                .rename(columns={"categoria":"Setor","nome":"Produto","lead_time":"Entrega (d)","saldo_atual":"Saldo","Sugestão Compra":"Comprar"}),
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
                    pmp_at = float(df.loc[df["id"]==id_e,"valor_unitario"].values[0])
                    sal_e  = int(df.loc[df["id"]==id_e,"saldo_atual"].values[0])
                    c1, c2 = st.columns(2)
                    with c1: qe = st.number_input("Quantidade", min_value=1, key="e_q")
                    with c2: preco = st.number_input("Preço unit. (R$)", min_value=0.0, value=pmp_at, step=0.01, key="e_v")
                    obs_e = st.text_input("Nota/Fornecedor", key="e_obs")
                    st.info(f"Saldo atual: **{sal_e}** → após entrada: **{sal_e + int(qe)}**")
                    if st.button("Confirmar Entrada", type="secondary"):
                        ok, msg = registrar_entrada(id_e, int(qe), preco, obs_e)
                        if ok:
                            disparar_sync()
                            st.toast(f"📥 {msg}", icon="✅")
                            st.rerun()
                        else:
                            st.error(msg)

            with col_s:
                with st.container(border=True):
                    st.subheader("📤 Saída")
                    sel_s = st.selectbox("Produto ", list(ops.keys()), key="s_p")
                    id_s  = ops[sel_s]
                    max_s = int(df.loc[df["id"]==id_s,"saldo_atual"].values[0])
                    c1, c2 = st.columns(2)
                    with c1: qs = st.number_input("Quantidade", min_value=1, key="s_q")
                    with c2: obs_s = st.text_input("Destino/Obs", key="s_obs")
                    bloqueado = int(qs) > max_s
                    if bloqueado: st.error(f"❌ Saldo insuficiente: {max_s} un disponíveis.")
                    if st.button("Confirmar Saída", type="primary", disabled=bloqueado):
                        ok, msg = registrar_saida(id_s, int(qs), obs_s)
                        if ok:
                            disparar_sync()
                            st.toast(f"📤 {msg}", icon="🚀")
                            st.rerun()
                        else:
                            st.error(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # INVENTÁRIO
    # ══════════════════════════════════════════════════════════════════════════
    with aba_inv:
        st.subheader("📋 Auditoria de Inventário")
        df = listar_produtos()
        if not df.empty:
            hoje = _hoje_str()
            with get_conn() as conn:
                contados_hoje = pd.read_sql(
                    "SELECT id_produto FROM movimentacoes WHERE tipo='Contagem' AND data_hora LIKE ?",
                    conn, params=(f"{hoje}%",),
                )
            ids_hoje = set(contados_hoje["id_produto"].tolist())

            with st.container(border=True):
                ops_inv = {}
                for _, row in df.iterrows():
                    label = f"✅ {row['nome']} (auditado hoje)" if row["id"] in ids_hoje else row["nome"]
                    ops_inv[label] = row["id"]
                sel_c = st.selectbox("Insumo para contagem", list(ops_inv.keys()), key="c_p")
                id_c  = ops_inv[sel_c]
                s_sis = int(df.loc[df["id"]==id_c,"saldo_atual"].values[0])
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
            prod_lista = ["Todos"] + list(df["nome"].unique())
            prod_sel   = st.selectbox("Filtrar por produto", prod_lista)
            q_base = """
                SELECT m.data_hora AS "Data/Hora", p.nome AS "Produto",
                       (m.saldo_resultante - m.quantidade) AS "Anterior",
                       m.saldo_resultante AS "Físico",
                       m.quantidade AS "Divergência", m.observacao AS "Registro"
                FROM movimentacoes m JOIN produtos p ON p.id=m.id_produto
                WHERE m.tipo='Contagem'
            """
            params: list = []
            if prod_sel != "Todos":
                q_base += " AND p.nome=?"
                params.append(prod_sel)
            q_base += " ORDER BY m.id DESC LIMIT 15"
            with get_conn() as conn:
                hist_inv = pd.read_sql(q_base, conn, params=params)
            if not hist_inv.empty:
                def cor_div(val):
                    if val < 0: return "color:#ef4444;font-weight:bold"
                    if val > 0: return "color:#10b859;font-weight:bold"
                    return "color:#94a3b8"
                st.dataframe(hist_inv.style.map(cor_div, subset=["Divergência"]), hide_index=True, use_container_width=True)

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
                item_an  = st.selectbox("Produto", list(df["nome"].unique()))
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
                    st.info("Sem entradas registradas para este produto.")

            st.divider()
            st.markdown("##### Filtro de período")
            col_f1, col_f2, col_f3, col_f4 = st.columns(4)
            with col_f1:
                data_ini = st.date_input("De", value=_now_dt().date() - timedelta(days=30), key="h_ini")
            with col_f2:
                data_fim = st.date_input("Até", value=_now_dt().date(), key="h_fim")
            with col_f3:
                tipo_sel = st.selectbox("Tipo", ["Todos"] + list(mv["tipo"].dropna().unique()), key="h_tipo")
            with col_f4:
                prod_fil = st.selectbox("Produto", ["Todos"] + list(mv["produto"].dropna().unique()), key="h_prod")

            mv_f = mv.copy()
            mv_f["_data"] = mv_f["data_hora"].str[:10]   # dd/mm/yyyy
            di_str = data_ini.strftime(FMT_DATE)
            df_str = data_fim.strftime(FMT_DATE)

            def _parse_br(s):
                try:
                    return datetime.strptime(s, FMT_DATE)
                except Exception:
                    return None

            mv_f["_dt"] = mv_f["_data"].apply(_parse_br)
            dt_ini = datetime.strptime(di_str, FMT_DATE)
            dt_fim = datetime.strptime(df_str, FMT_DATE)
            mv_f = mv_f[mv_f["_dt"].between(dt_ini, dt_fim)].drop(columns=["_data","_dt"])

            if tipo_sel != "Todos":
                mv_f = mv_f[mv_f["tipo"] == tipo_sel]
            if prod_fil != "Todos":
                mv_f = mv_f[mv_f["produto"] == prod_fil]

            st.caption(f"{len(mv_f)} registro(s) no período de {di_str} a {df_str}.")
            st.dataframe(mv_f, use_container_width=True, hide_index=True)

            if not mv_f.empty:
                nome_arq = f"historico_{di_str.replace('/','_')}_a_{df_str.replace('/','_')}.csv"
                st.download_button(
                    label="⬇️ Exportar CSV",
                    data=mv_f.to_csv(index=False).encode("utf-8-sig"),
                    file_name=nome_arq,
                    mime="text/csv",
                )

    # ══════════════════════════════════════════════════════════════════════════
    # ABAS ADMIN
    # ══════════════════════════════════════════════════════════════════════════
    if is_admin:
        aba_ia, aba_cfg = abas[4], abas[5]

        # ════════════════════════════════════════════════════════════════════
        # [5] IA — CHAT CONTÍNUO COM MEMÓRIA DE CONTEXTO
        # ════════════════════════════════════════════════════════════════════
        with aba_ia:
            st.subheader("🧠 Assistente IA de Suprimentos")
            try:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                modelos = [
                    m.name.replace("models/", "")
                    for m in genai.list_models()
                    if "generateContent" in m.supported_generation_methods
                ]

                col_m, col_btn = st.columns([3, 1])
                with col_m:
                    modelo = st.selectbox("Modelo", modelos, key="ia_modelo")
                with col_btn:
                    st.write("")
                    if st.button("🗑️ Nova conversa", use_container_width=True):
                        st.session_state["ia_hist"] = []
                        st.rerun()

                if "ia_hist" not in st.session_state:
                    st.session_state["ia_hist"] = []

                # Correção: Otimizado injeção com tabela Markdown limpa para o Gemini ler melhor
                def _ctx_estoque() -> str:
                    df_ia   = listar_produtos()
                    cons_ia = calcular_consumo_mensal()
                    df_ia   = df_ia.merge(cons_ia, left_on="id", right_on="id_produto", how="left").fillna(0)
                    df_ia["consumo_mensal"] = df_ia["total"].astype(int)
                    
                    # Converte para Markdown estruturado (ideal para LLMs estruturarem raciocínio)
                    tabela = df_ia[["categoria","nome","saldo_atual","estoque_minimo","lead_time","consumo_mensal"]].to_markdown(index=False)
                    return (
                        "Você é um assistente especialista em logística e gestão de armazém. "
                        "Responda sempre em português brasileiro, de forma direta e objetiva. "
                        "Abaixo está a posição atual do estoque estruturada:\n\n"
                        f"{tabela}\n\n"
                        "Use esses dados para responder as perguntas do operador."
                    )

                for msg in st.session_state["ia_hist"]:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

                if not st.session_state["ia_hist"]:
                    st.caption("Faça uma pergunta ou gere um diagnóstico completo:")
                    if st.button("✨ Diagnóstico completo do estoque", use_container_width=True):
                        st.session_state["ia_hist"].append({
                            "role": "user",
                            "content": "Faça um diagnóstico completo: resumo de saúde, riscos de ruptura antes do lead time e sugestão de compras prioritárias."
                        })
                        st.rerun()

                pergunta = st.chat_input("Pergunte sobre o estoque...")
                if pergunta:
                    st.session_state["ia_hist"].append({"role": "user", "content": pergunta})

                    ctx      = _ctx_estoque()
                    hist     = st.session_state["ia_hist"]
                    api_hist = []

                    for i, msg in enumerate(hist):
                        role = "user" if msg["role"] == "user" else "model"
                        if i == 0:
                            content = f"{ctx}\n\n---\nPergunta do operador:\n{msg['content']}"
                        else:
                            content = msg["content"]
                        api_hist.append({"role": role, "parts": [content]})

                    with st.spinner("Analisando..."):
                        mod  = genai.GenerativeModel(modelo)
                        chat = mod.start_chat(history=api_hist[:-1])
                        resp = chat.send_message(api_hist[-1]["parts"][0])

                    st.session_state["ia_hist"].append({"role": "assistant", "content": resp.text})
                    st.rerun()

            except Exception as e:
                st.error(f"Erro na API do Google: {e}")

        # ════════════════════════════════════════════════════════════════════
        # CONFIG
        # ════════════════════════════════════════════════════════════════════
        with aba_cfg:
            st.markdown("### Aprovação de novos operadores")
            with get_conn() as conn:
                pendentes = pd.read_sql("SELECT usuario, pergunta_seguranca FROM usuarios WHERE aprovado=0", conn)

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
                                conn.execute("UPDATE usuarios SET aprovado=1, perfil=? WHERE usuario=?", (perf_alvo, usr_alvo))
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
                ativos = pd.read_sql("SELECT usuario, perfil FROM usuarios WHERE aprovado=1", conn)

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
                                conn.execute("UPDATE usuarios SET perfil=? WHERE usuario=?", (novo_perf, usr_ed))
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
                    p_at  = df_cfg[df_cfg["id"]==id_e].iloc[0]
                    with st.form("edit_p"):
                        en = st.text_input("Nome", value=p_at["nome"])
                        ec = st.selectbox("Setor", CATEGORIAS,
                                          index=CATEGORIAS.index(p_at["categoria"]) if p_at["categoria"] in CATEGORIAS else 0)
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