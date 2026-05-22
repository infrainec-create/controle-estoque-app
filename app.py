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
# CONSTANTES E CONFIGURAÇÕES
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH, TIMEZONE, FMT_DATETIME, FMT_DATE = "estoque.db", "America/Fortaleza", "%d/%m/%Y %H:%M", "%d/%m/%Y"
CACHE_TTL, CATEGORIAS, PERFIS, RUPTURA_LIMITE = 30, ["Limpeza", "Copa", "EPI", "Escritório", "Geral"], ["Operador", "Administrador"], 0
SESSION_TTL_H, MAX_TENTATIVAS, BLOQUEIO_MIN = 8, 5, 30

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wms")

st.set_page_config(page_title="WMS 4.0", page_icon="📦", layout="wide")
st.markdown("""<style>
.stButton>button { border-radius:10px; font-weight:600; height:3em; width:100%; margin-top:10px; }
.metric-card { padding:20px; border-radius:12px; box-shadow:0 4px 6px rgba(0,0,0,.1); margin-bottom:15px; }
#MainMenu, footer { visibility:hidden; }
</style>""", unsafe_allow_html=True)

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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessoes (token TEXT PRIMARY KEY, usuario TEXT NOT NULL, data_criacao TEXT NOT NULL, expira_em TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tentativas_login (usuario TEXT PRIMARY KEY, contador INTEGER DEFAULT 0, ultimo_erro TEXT);
            CREATE TABLE IF NOT EXISTS usuarios (usuario TEXT PRIMARY KEY, senha_hash TEXT NOT NULL, pergunta_seguranca TEXT NOT NULL, resposta_seguranca_hash TEXT NOT NULL, aprovado INTEGER DEFAULT 0, perfil TEXT DEFAULT 'Operador');
            CREATE TABLE IF NOT EXISTS produtos (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE, saldo_atual INTEGER NOT NULL DEFAULT 0, estoque_minimo INTEGER DEFAULT 10, valor_unitario REAL DEFAULT 0, categoria TEXT DEFAULT 'Geral', lead_time INTEGER DEFAULT 3);
            CREATE TABLE IF NOT EXISTS movimentacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, id_produto INTEGER NOT NULL REFERENCES produtos(id), data_hora TEXT NOT NULL, tipo TEXT NOT NULL CHECK(tipo IN ('Entrada','Saída','Ajuste','Contagem')), quantidade INTEGER NOT NULL, saldo_resultante INTEGER NOT NULL, observacao TEXT);
        """)
        for col, table, ddl in [("expira_em", "sessoes", "ALTER TABLE sessoes ADD COLUMN expira_em TEXT DEFAULT '2099-01-01 00:00'"), ("aprovado", "usuarios", "ALTER TABLE usuarios ADD COLUMN aprovado INTEGER DEFAULT 0"), ("perfil", "usuarios", "ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'Operador'")]:
            try: conn.execute(ddl)
            except sqlite3.OperationalError: pass
        conn.execute("UPDATE usuarios SET perfil='Administrador' WHERE usuario='admin'")

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS E SEGURANÇA
# ─────────────────────────────────────────────────────────────────────────────
def _now_dt() -> datetime: return datetime.now(ZoneInfo(TIMEZONE))
def _now_str() -> str: return _now_dt().strftime(FMT_DATETIME)
def _hoje_str() -> str: return _now_dt().strftime(FMT_DATE)
def _dt_str(dt: datetime) -> str: return dt.strftime(FMT_DATETIME)
def _str_dt(s: str) -> datetime: return datetime.strptime(s, FMT_DATETIME).replace(tzinfo=ZoneInfo(TIMEZONE))
def hash_senha(s: str) -> str: return hashlib.sha256(s.encode()).hexdigest()

def _criar_sessao(usuario: str) -> str:
    token, agora = str(uuid.uuid4()), _now_dt()
    expira = agora + timedelta(hours=SESSION_TTL_H)
    with get_conn() as conn:
        conn.execute("DELETE FROM sessoes WHERE usuario=? OR expira_em < ?", (usuario, _dt_str(agora)))
        conn.execute("INSERT INTO sessoes VALUES (?,?,?,?)", (token, usuario, _dt_str(agora), _dt_str(expira)))
    return token

def _validar_token(token: str):
    with get_conn() as conn:
        return conn.execute("SELECT s.usuario, u.perfil, s.expira_em FROM sessoes s JOIN usuarios u ON s.usuario=u.usuario WHERE s.token=? AND s.expira_em > ?", (token, _dt_str(_now_dt()))).fetchone()

def _revogar_token(token: str) -> None:
    with get_conn() as conn: conn.execute("DELETE FROM sessoes WHERE token=?", (token,))

def _verificar_bloqueio(usuario: str) -> tuple[bool, int]:
    with get_conn() as conn: row = conn.execute("SELECT contador, ultimo_erro FROM tentativas_login WHERE usuario=?", (usuario,)).fetchone()
    if not row or row[0] < MAX_TENTATIVAS or not row[1]: return False, 0
    try:
        bloqueado_ate = _str_dt(row[1]) + timedelta(minutes=BLOQUEIO_MIN)
        if _now_dt() < bloqueado_ate: return True, int((bloqueado_ate - _now_dt()).total_seconds() / 60) + 1
        with get_conn() as conn: conn.execute("DELETE FROM tentativas_login WHERE usuario=?", (usuario,))
        return False, 0
    except Exception: return False, 0

def _registrar_falha(usuario: str) -> int:
    with get_conn() as conn:
        conn.execute("INSERT INTO tentativas_login VALUES (?,1,?) ON CONFLICT(usuario) DO UPDATE SET contador=contador+1, ultimo_erro=excluded.ultimo_erro", (usuario, _now_str()))
        return conn.execute("SELECT contador FROM tentativas_login WHERE usuario=?", (usuario,)).fetchone()[0]

def _zerar_tentativas(usuario: str) -> None:
    with get_conn() as conn: conn.execute("DELETE FROM tentativas_login WHERE usuario=?", (usuario,))

def _login(usuario: str, senha: str) -> tuple[bool, str, str]:
    bloqueado, mins = _verificar_bloqueio(usuario)
    if bloqueado: return False, f"bloqueado:{mins}", ""
    with get_conn() as conn: row = conn.execute("SELECT aprovado, perfil FROM usuarios WHERE usuario=? AND senha_hash=?", (usuario, hash_senha(senha))).fetchone()
    if not row: return False, f"invalido:{max(0, MAX_TENTATIVAS - _registrar_falha(usuario))}", ""
    if row[0] != 1: return False, "pendente", ""
    _zerar_tentativas(usuario)
    return True, usuario, row[1]

# ─────────────────────────────────────────────────────────────────────────────
# CACHE E LÓGICA DE NEGÓCIO
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=CACHE_TTL)
def listar_produtos() -> pd.DataFrame:
    with get_conn() as conn: return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)

@st.cache_data(ttl=CACHE_TTL)
def listar_movimentacoes() -> pd.DataFrame:
    with get_conn() as conn: return pd.read_sql("SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao FROM movimentacoes m JOIN produtos p ON p.id=m.id_produto ORDER BY m.id DESC", conn)

@st.cache_data(ttl=CACHE_TTL)
def calcular_consumo_mensal() -> pd.DataFrame:
    with get_conn() as conn: return pd.read_sql("SELECT id_produto, SUM(ABS(quantidade)) AS total FROM movimentacoes WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0) GROUP BY id_produto", conn)

def invalidar_cache() -> None:
    listar_produtos.clear(); listar_movimentacoes.clear(); calcular_consumo_mensal.clear()

def _reg_mov(conn, id_produto, tipo, quantidade, saldo_resultante, obs=""):
    conn.execute("INSERT INTO movimentacoes (id_produto,data_hora,tipo,quantidade,saldo_resultante,observacao) VALUES (?,?,?,?,?,?)", (id_produto, _now_str(), tipo, quantidade, saldo_resultante, obs))

def registrar_entrada(id_produto, quantidade, preco_compra, obs=""):
    try:
        with get_conn() as conn:
            saldo_ant, pmp_ant = conn.execute("SELECT saldo_atual, valor_unitario FROM produtos WHERE id=?", (id_produto,)).fetchone()
            total_novo = saldo_ant + quantidade
            novo_pmp = ((saldo_ant * pmp_ant) + (quantidade * preco_compra)) / total_novo if total_novo > 0 else preco_compra
            conn.execute("UPDATE produtos SET saldo_atual=saldo_atual+?, valor_unitario=? WHERE id=?", (quantidade, novo_pmp, id_produto))
            _reg_mov(conn, id_produto, "Entrada", quantidade, total_novo, f"{obs} | Pago: R$ {preco_compra:.2f}/un".strip(" |"))
        return True, f"Novo PMP: R$ {novo_pmp:.2f}"
    except Exception as e: return False, str(e)

def registrar_saida(id_produto, quantidade, obs=""):
    try:
        with get_conn() as conn:
            saldo = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            if quantity := quantidade > saldo: return False, f"Estoque insuficiente. Saldo: {saldo}"
            conn.execute("UPDATE produtos SET saldo_atual=saldo_atual-? WHERE id=?", (quantidade, id_produto))
            _reg_mov(conn, id_produto, "Saída", -quantidade, saldo - quantity, obs)
        return True, f"Novo saldo: {saldo - quantity}"
    except Exception as e: return False, str(e)

def registrar_contagem(id_produto, fisico, operador):
    try:
        with get_conn() as conn:
            sis = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            conn.execute("UPDATE produtos SET saldo_atual=? WHERE id=?", (fisico, id_produto))
            _reg_mov(conn, id_produto, "Contagem", fisico - sis, fisico, f"Inventário | Op: {operador}")
        return True, f"Divergência: {fisico - sis:+d} un. Novo saldo: {fisico}"
    except Exception as e: return False, str(e)

def registrar_ajuste(id_produto, novo_saldo, motivo=""):
    try:
        with get_conn() as conn:
            ant = conn.execute("SELECT saldo_atual FROM produtos WHERE id=?", (id_produto,)).fetchone()[0]
            conn.execute("UPDATE produtos SET saldo_atual=? WHERE id=?", (novo_saldo, id_produto))
            _reg_mov(conn, id_produto, "Ajuste", novo_saldo - ant, novo_saldo, motivo)
        return True, f"Saldo adjusted para {novo_saldo}"
    except Exception as e: return False, str(e)

def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn: conn.execute("INSERT INTO produtos (nome,saldo_atual,estoque_minimo,valor_unitario,categoria,lead_time) VALUES (?,0,?,?,?,?)", (nome, estoque_minimo, valor_unitario, categoria, lead_time))
        return True, f'"{nome}" cadastrado.'
    except sqlite3.IntegrityError: return False, f'"{nome}" já existe.'
    except Exception as e: return False, str(e)

def editar_produto(id_p, nome, min_e, valor, cat, lead):
    try:
        with get_conn() as conn: conn.execute("UPDATE produtos SET nome=?,estoque_minimo=?,valor_unitario=?,categoria=?,lead_time=? WHERE id=?", (nome, min_e, valor, cat, lead, id_p))
        return True, "Atualizado."
    except Exception as e: return False, str(e)

def deletar_produto(id_produto):
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM movimentacoes WHERE id_produto=?", (id_produto,))
            conn.execute("DELETE FROM produtos WHERE id=?", (id_produto,))
        return True, "Removido."
    except Exception as e: return False, str(e)

# ─────────────────────────────────────────────────────────────────────────────
# SYNC GOOGLE DRIVE
# ─────────────────────────────────────────────────────────────────────────────
def _drive_svc():
    return build("drive", "v3", credentials=service_account.Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"])))

def _upsert_drive(svc, name, media):
    files = svc.files().list(q=f"name='{name}' and '{FOLDER_ID}' in parents and trashed=false", fields="files(id)").execute().get("files", [])
    if files: svc.files().update(fileId=files[0]["id"], media_body=media).execute()
    else: svc.files().create(body={"name": name, "parents": [FOLDER_ID]}, media_body=media).execute()

def _executar_sync():
    backup_file = "estoque_backup.db"
    try:
        svc = _drive_svc()
        with get_conn() as conn:
            bck = sqlite3.connect(backup_file)
            conn.backup(bck); bck.close()
        _upsert_drive(svc, DB_PATH, MediaFileUpload(backup_file, mimetype="application/x-sqlite3", resumable=True))
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs = pd.read_sql("SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao FROM movimentacoes m JOIN produtos p ON p.id=m.id_produto ORDER BY m.id DESC", conn)
        for df_e, fname in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            _upsert_drive(svc, fname, MediaIoBaseUpload(BytesIO(df_e.to_csv(index=False).encode("utf-8-sig")), mimetype="text/csv"))
        st.session_state.update(ultima_sync=_now_str(), sync_erro=None)
    except Exception as e: st.session_state["sync_erro"] = str(e)
    finally:
        if os.path.exists(backup_file): os.remove(backup_file)

def disparar_sync():
    invalidar_cache()
    threading.Thread(target=_executar_sync, daemon=True).start()

def descarregar_do_drive():
    try:
        svc = _drive_svc()
        res = svc.files().list(q=f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false", fields="files(id)").execute()
        if res.get("files"):
            req = svc.files().get_media(fileId=res["files"][0]["id"])
            with open(DB_PATH, "wb") as f:
                dl = MediaIoBaseDownload(f, req)
                done = False
                while not done: _, done = dl.next_chunk()
            return True
    except Exception: pass
    return False

# ─────────────────────────────────────────────────────────────────────────────
# CONTROLE DE INTERFACE
# ─────────────────────────────────────────────────────────────────────────────
if "db_ok" not in st.session_state:
    if not os.path.exists(DB_PATH): descarregar_do_drive()
    init_db(); st.session_state.update(db_ok=True, ultima_sync=None, sync_erro=None)

if "autenticado" not in st.session_state:
    st.session_state.update(autenticado=False, usuario_atual="", perfil_atual="", token_expira="")
    if token := st.query_params.get("token"):
        if sessao := _validar_token(token): st.session_state.update(autenticado=True, usuario_atual=sessao[0], perfil_atual=sessao[1], token_expira=sessao[2])
        else: st.query_params.clear()

if not st.session_state["autenticado"]:
    st.title("🔒 WMS — Controle de Acesso")
    aba_login, aba_cadastro, aba_recuperar = st.tabs(["🔑 Entrar", "👤 Criar Conta", "🛠️ Esqueci a Senha"])
    
    with aba_login:
        with st.form("form_login"):
            usr, pwd = st.text_input("Usuário").strip(), st.text_input("Senha", type="password")
            if st.form_submit_button("Acessar WMS") and usr and pwd:
                ok, u, perf = _login(usr, pwd)
                if ok:
                    st.query_params["token"] = _criar_sessao(u)
                    st.session_state.update(autenticado=True, usuario_atual=u, perfil_atual=perf, token_expira=_validar_token(st.query_params["token"])[2])
                    st.toast(f"Bem-vindo, {u}!", icon="👋"); st.rerun()
                elif u.startswith("bloqueado:"): st.error(f"🔒 Conta bloqueada. Aguarde {u.split(':')[1]} minuto(s).")
                elif u.startswith("invalido:"): st.error(f"❌ Incorreto. {u.split(':')[1]} tentativa(s) restante(s)." if int(u.split(':')[1]) > 0 else f"🔒 Limite atingido. Aguarde {BLOQUEIO_MIN} min.")
                elif u == "pendente": st.error("⏳ Aguardando aprovação do administrador.")

    with aba_cadastro:
        with st.form("form_cadastro"):
            new_usr, new_pwd = st.text_input("Nome de usuário").strip(), st.text_input("Senha", type="password")
            pergunta = st.selectbox("Pergunta", ["Qual o nome do seu primeiro animal de estimação?", "Qual a sua cidade natal?", "Qual o nome da sua mãe?"])
            resposta = st.text_input("Resposta").strip().lower()
            if st.form_submit_button("Enviar") and new_usr and new_pwd and resposta:
                is_adm = new_usr.lower() == "admin"
                try:
                    with get_conn() as conn: conn.execute("INSERT INTO usuarios VALUES (?,?,?,?,?,?)", (new_usr, hash_senha(new_pwd), pergunta, hash_senha(resposta), 1 if is_adm else 0, "Administrador" if is_adm else "Operador"))
                    disparar_sync(); st.success("👑 Criado!" if is_adm else "⏳ Solicitado.")
                except sqlite3.IntegrityError: st.error("Usuário já existe.")

    with aba_recuperar:
        if usr_rec := st.text_input("Usuário").strip():
            with get_conn() as conn: row = conn.execute("SELECT pergunta_seguranca FROM usuarios WHERE usuario=?", (usr_rec,)).fetchone()
            if row:
                st.info(f"Pergunta: {row[0]}")
                resp, n_pwd = st.text_input("Resposta", type="password").strip().lower(), st.text_input("Nova senha", type="password")
                if st.button("Gravar") and resp and n_pwd:
                    with get_conn() as conn: ok = conn.execute("SELECT 1 FROM usuarios WHERE usuario=? AND resposta_seguranca_hash=?", (usr_rec, hash_senha(resp))).fetchone()
                    if ok:
                        with get_conn() as conn: conn.execute("UPDATE usuarios SET senha_hash=? WHERE usuario=?", (hash_senha(n_pwd), usr_rec)); conn.execute("DELETE FROM sessoes WHERE usuario=?", (usr_rec,))
                        disparar_sync(); st.success("✅ Redefinida!")
                    else: st.error("Resposta incorreta.")

else:
    is_admin = st.session_state["perfil_atual"] == "Administrador"
    token_atual = st.query_params.get("token", "")

    with st.sidebar:
        st.write(f"👤 **{st.session_state['usuario_atual']}** ({st.session_state['perfil_atual']})")
        st.caption(f"⏱️ Expira: {st.session_state.get('token_expira','')}")
        st.caption(f"☁️ Sync: {st.session_state.get('ultima_sync','Pendente')}")
        if st.session_state.get("sync_erro"): st.warning(st.session_state["sync_erro"])
        if st.button("🔄 Sincronizar"): disparar_sync(); st.toast("Iniciado!")
        if st.button("🚪 Sair", type="primary"):
            if token_atual: _revogar_token(token_atual)
            st.query_params.clear(); st.session_state.update(autenticado=False); st.rerun()

    nomes_abas = ["📊 Painel", "⚡ Saídas/Entradas", "📋 Inventário", "📜 Histórico"] + (["🧠 IA Analista", "⚙️ Config"] if is_admin else [])
    abas = st.tabs(nomes_abas)
    aba_painel, aba_op, aba_inv, aba_hist = abas[:4]

    with aba_painel:
        df, cons = listar_produtos(), calcular_consumo_mensal()
        if not df.empty:
            df = df.merge(cons, left_on="id", right_on="id_produto", how="left").fillna(0)
            df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]
            df["consumo_diario"] = df["total"] / 30
            df["Runway"] = df.apply(lambda r: int(r["saldo_atual"] / r["consumo_diario"]) if r["consumo_diario"] > 0 else 999, axis=1)
            df["Status"] = df.apply(lambda r: "🔴 Ruptura" if r["saldo_atual"] <= 0 else ("🔴 Crítico" if r["saldo_atual"] < r["estoque_minimo"] else ("orange: Risco" if r["Runway"] <= r["lead_time"] else "🟢 OK")), axis=1)
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Categorias", df["categoria"].nunique())
            c2.metric("Valor Total", f"R$ {df['valor_total'].sum():,.2f}")
            c3.metric("Críticos/Ruptura", int((df["saldo_atual"] < df["estoque_minimo"]).sum()))
            c4.metric("Giro Total", f"{int(df['total'].sum())} un")

            setor_sel = st.selectbox("Setor", ["Todos"] + list(df["categoria"].unique()))
            busca = st.text_input("🔍 Buscar Produto")
            df_f = df.copy()
            if setor_sel != "Todos": df_f = df_f[df_f["categoria"] == setor_sel]
            if busca.strip(): df_f = df_f[df_f["nome"].str.contains(busca, case=False)]

            st.dataframe(df_f[["Status","categoria","nome","saldo_atual","valor_unitario","estoque_minimo"]], use_container_width=True, hide_index=True)
            
            g1, g2 = st.columns(2)
            with g1:
                giro = df.groupby("categoria")["total"].sum().reset_index()
                if giro["total"].sum() > 0: st.bar_chart(giro, x="categoria", y="total")
                else: st.info("Sem saídas.")
            with g2:
                top = df[df["total"] > 0].nlargest(5, "total")
                if not top.empty: st.bar_chart(top, x="nome", y="total")
                else: st.info("Sem consumo.")

    with aba_op:
        df = listar_produtos()
        if not df.empty:
            ops = dict(zip(df["nome"], df["id"]))
            col_e, col_s = st.columns(2)
            with col_e:
                with st.container(border=True):
                    sel_e = st.selectbox("Insumo Entrada", list(ops.keys()))
                    id_e = ops[sel_e]
                    qe = st.number_input("Qtd Entrada", min_value=1, key="eq")
                    preco = st.number_input("Preço", min_value=0.0, value=float(df.loc[df["id"]==id_e,"valor_unitario"].values[0]))
                    if st.button("Confirmar Entrada"):
                        ok, m = registrar_entrada(id_e, int(qe), preco, st.text_input("Obs", key="oe"))
                        if ok: disparar_sync(); st.rerun()
            with col_s:
                with st.container(border=True):
                    sel_s = st.selectbox("Insumo Saída", list(ops.keys()))
                    id_s = ops[sel_s]
                    qs = st.number_input("Qtd Saída", min_value=1, key="qs")
                    if st.button("Confirmar Saída"):
                        ok, m = registrar_saida(id_s, int(qs), st.text_input("Obs", key="os"))
                        if ok: disparar_sync(); st.rerun()
                        else: st.error(m)

    with aba_inv:
        df = listar_produtos()
        if not df.empty:
            sel_c = st.selectbox("Auditar", list(df["nome"]))
            id_c = df.loc[df["nome"]==sel_c, "id"].values[0]
            f_cont = st.number_input("Físico Contado", min_value=0, step=1)
            if st.button("💾 Salvar Auditoria"):
                ok, msg = registrar_contagem(id_c, int(f_cont), st.session_state["usuario_atual"])
                if ok: disparar_sync(); st.toast(msg); st.rerun()

    with aba_hist:
        mv = listar_movimentacoes()
        if not mv.empty:
            st.dataframe(mv, use_container_width=True, hide_index=True)

    if is_admin:
        aba_ia, aba_cfg = abas[4], abas[5]
        with aba_ia:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            pergunta = st.chat_input("Pergunte à IA...")
            if pergunta:
                mod = genai.GenerativeModel("gemini-pro")
                ctx = listar_produtos().to_markdown(index=False)
                resp = mod.generate_content(f"Contexto:\n{ctx}\n\nPergunta: {pergunta}")
                st.write(resp.text)

        with aba_cfg:
            with get_conn() as conn: pendentes = pd.read_sql("SELECT usuario, perfil FROM usuarios WHERE aprovado=0", conn)
            if not pendentes.empty:
                st.dataframe(pendentes, use_container_width=True, hide_index=True)
                u_alvo = st.selectbox("Tratar Usuário", list(pendentes["usuario"]))
                if st.button("Aprovar"):
                    with get_conn() as conn: conn.execute("UPDATE usuarios SET aprovado=1 WHERE usuario=?", (u_alvo,))
                    disparar_sync(); st.rerun()